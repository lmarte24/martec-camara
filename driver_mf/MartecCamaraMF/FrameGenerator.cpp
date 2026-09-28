// Martec Camara: fuente de cuadros por memoria compartida.
// Reemplaza el FrameGenerator de VCamSample (que dibujaba con Direct2D).

#include "pch.h"
#include <sddl.h>
#include "Undocumented.h"
#include "Tools.h"
#include "EnumNames.h"
#include "MFTools.h"
#include "FrameGenerator.h"

#pragma comment(lib, "advapi32")

// Global primero: la fuente corre dentro del servicio Frame Server (sesion 0),
// y la app en la sesion del usuario. Si este proceso no puede crear objetos
// globales (por ejemplo, cargado dentro de una app comun), se usa Local.
static const wchar_t* const NOMBRES[] = { L"Global\\MartecCamaraMF", L"Local\\MartecCamaraMF" };

// Pueden leer y escribir: SYSTEM, Local Service, Network Service,
// administradores y usuarios interactivos. La etiqueta de integridad media
// permite que la app (integridad media) escriba en un objeto creado por un
// servicio (integridad de sistema).
static const wchar_t* const SDDL_MAPA =
	L"D:(A;;GA;;;SY)(A;;GA;;;LS)(A;;GA;;;NS)(A;;GA;;;BA)(A;;GA;;;IU)S:(ML;;NW;;;ME)";

HRESULT FrameGenerator::EnsureRenderTarget(UINT width, UINT height)
{
	_width = width;
	_height = height;
	if (_header)
		return S_OK;

	const DWORD size = (DWORD)(sizeof(MartecFrameHeader) + (size_t)width * height * 4);
	PSECURITY_DESCRIPTOR sd = nullptr;
	SECURITY_ATTRIBUTES sa = { sizeof(sa), nullptr, FALSE };
	if (ConvertStringSecurityDescriptorToSecurityDescriptorW(SDDL_MAPA, SDDL_REVISION_1, &sd, nullptr))
	{
		sa.lpSecurityDescriptor = sd;
	}

	bool existia = false;
	for (auto nombre : NOMBRES)
	{
		_mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, &sa, PAGE_READWRITE, 0, size, nombre);
		auto err = GetLastError();
		if (_mapping)
		{
			existia = err == ERROR_ALREADY_EXISTS;
			WINTRACE(L"Martec: memoria compartida %s (%s)", nombre, existia ? L"ya existia" : L"nueva");
			break;
		}
		WINTRACE(L"Martec: CreateFileMapping %s fallo: %u", nombre, err);
	}
	if (sd)
	{
		LocalFree(sd);
	}
	if (!_mapping)
		return S_OK; // sin memoria compartida: se entrega la imagen de espera

	_header = static_cast<MartecFrameHeader*>(MapViewOfFile(_mapping, FILE_MAP_ALL_ACCESS, 0, 0, size));
	if (!_header)
	{
		WINTRACE(L"Martec: MapViewOfFile fallo: %u", GetLastError());
		CloseHandle(_mapping);
		_mapping = nullptr;
		return S_OK;
	}

	if (!existia || _header->magic != MARTEC_MAGIC || _header->width != width || _header->height != height)
	{
		ZeroMemory(_header, sizeof(MartecFrameHeader));
		_header->version = 1;
		_header->width = width;
		_header->height = height;
		_header->stride = width * 4;
		_header->format = 1;
		MemoryBarrier();
		_header->magic = MARTEC_MAGIC;
	}
	_header->readerTick = (LONG64)GetTickCount64();
	return S_OK;
}

void FrameGenerator::Close()
{
	if (_header)
	{
		UnmapViewOfFile(_header);
		_header = nullptr;
	}
	if (_mapping)
	{
		CloseHandle(_mapping);
		_mapping = nullptr;
	}
}

// Imagen de espera: azul oscuro, el mismo tono que usa la app mientras
// enciende la webcam (BGR 60, 30, 12).
void FrameGenerator::Placeholder(BYTE* scanline0, LONG pitch, REFGUID format)
{
	if (format == MFVideoFormat_NV12)
	{
		for (UINT y = 0; y < _height; y++)
		{
			memset(scanline0 + (LONG_PTR)y * pitch, 28, _width);             // Y
		}
		for (UINT y = 0; y < _height / 2; y++)
		{
			auto uv = reinterpret_cast<UINT16*>(scanline0 + (LONG_PTR)(_height + y) * pitch);
			for (UINT x = 0; x < _width / 2; x++)
			{
				uv[x] = 0x7492;                                              // U=146, V=116
			}
		}
		return;
	}
	for (UINT y = 0; y < _height; y++)
	{
		auto fila = reinterpret_cast<UINT32*>(scanline0 + (LONG_PTR)y * pitch);
		for (UINT x = 0; x < _width; x++)
		{
			fila[x] = 0xFF0C1E3C;                                            // BGRA 60,30,12,255
		}
	}
}

HRESULT FrameGenerator::Generate(IMFSample* sample, REFGUID format, IMFSample** outSample)
{
	RETURN_HR_IF_NULL(E_POINTER, sample);
	RETURN_HR_IF_NULL(E_POINTER, outSample);
	*outSample = nullptr;

	if (!_header && _width && _height)
	{
		EnsureRenderTarget(_width, _height); // reintento, por si antes no se pudo
	}

	wil::com_ptr_nothrow<IMFMediaBuffer> mediaBuffer;
	RETURN_IF_FAILED(sample->GetBufferByIndex(0, &mediaBuffer));
	wil::com_ptr_nothrow<IMF2DBuffer2> buffer2D;
	BYTE* scanline;
	LONG pitch;
	BYTE* start;
	DWORD length;
	RETURN_IF_FAILED(mediaBuffer->QueryInterface(IID_PPV_ARGS(&buffer2D)));
	RETURN_IF_FAILED(buffer2D->Lock2DSize(MF2DBuffer_LockFlags_Write, &scanline, &pitch, &start, &length));

	HRESULT hr = S_OK;
	bool copiado = false;
	if (_header)
	{
		auto ahora = (LONG64)GetTickCount64();
		_header->readerTick = ahora;
		auto escrito = _header->writerTick;
		if (_header->magic == MARTEC_MAGIC && _header->width == _width && _header->height == _height &&
			escrito && ahora - escrito < MARTEC_WRITER_TIMEOUT_MS)
		{
			auto datos = reinterpret_cast<BYTE*>(_header) + sizeof(MartecFrameHeader);
			auto stride = (LONG)_header->stride;
			// seqlock: si la app estaba escribiendo (impar) o escribio durante la
			// copia (cambio el numero), se reintenta; al tercer intento se acepta.
			for (int intento = 0; intento < 3; intento++)
			{
				auto s1 = _header->seq;
				if ((s1 & 1) && intento < 2)
				{
					Sleep(0);
					continue;
				}
				MemoryBarrier();
				if (format == MFVideoFormat_NV12)
				{
					hr = RGB32ToNV12(datos, (ULONG)(stride * _height), stride, _width, _height, scanline, length, pitch);
				}
				else
				{
					for (UINT y = 0; y < _height; y++)
					{
						CopyMemory(scanline + (LONG_PTR)y * pitch, datos + (size_t)y * stride, (size_t)_width * 4);
					}
				}
				MemoryBarrier();
				copiado = SUCCEEDED(hr);
				if (!copiado || _header->seq == s1)
					break;
			}
		}
	}
	if (!copiado)
	{
		hr = S_OK;
		Placeholder(scanline, pitch, format);
	}
	buffer2D->Unlock2D();

	if (SUCCEEDED(hr))
	{
		_frame++;
		sample->AddRef();
		*outSample = sample;
	}
	return hr;
}
