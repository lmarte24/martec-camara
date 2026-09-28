#pragma once

// Martec Camara: en lugar de dibujar una imagen de prueba (como VCamSample),
// cada cuadro se copia desde la memoria compartida donde la app Martec Camara
// deja la imagen ya procesada (encuadre y retoque).
//
// Formato de la memoria compartida "Global\MartecCamaraMF" (o "Local\" si este
// proceso no puede crear objetos globales):
//   cabecera de 64 bytes (MartecFrameHeader) + imagen BGRA 32 bits, de arriba
//   hacia abajo, width * height * 4 bytes.
// La crea esta fuente al arrancar el stream (cuando una app abre la camara) y
// la cierra al pararlo: asi la app sabe cuando alguien esta usando la camara.

#pragma pack(push, 8)
struct MartecFrameHeader
{
	UINT32 magic;               // 'MCAM' = 0x4D41434D
	UINT32 version;             // 1
	UINT32 width;
	UINT32 height;
	UINT32 stride;              // bytes por fila = width * 4
	UINT32 format;              // 1 = BGRA 32 bits de arriba hacia abajo
	volatile LONG64 seq;        // seqlock: impar mientras la app escribe
	volatile LONG64 writerTick; // GetTickCount64() de la ultima escritura de la app
	volatile LONG64 readerTick; // GetTickCount64() del ultimo cuadro leido por la fuente
	UINT64 reserved[2];
};
#pragma pack(pop)
static_assert(sizeof(MartecFrameHeader) == 64, "la cabecera debe medir 64 bytes");

#define MARTEC_MAGIC 0x4D41434D
#define MARTEC_WRITER_TIMEOUT_MS 2000

class FrameGenerator
{
	UINT _width;
	UINT _height;
	ULONGLONG _frame;
	HANDLE _mapping;
	MartecFrameHeader* _header;

	void Placeholder(BYTE* scanline0, LONG pitch, REFGUID format);

public:
	FrameGenerator() :
		_width(0),
		_height(0),
		_frame(0),
		_mapping(nullptr),
		_header(nullptr)
	{
	}

	~FrameGenerator()
	{
		Close();
	}

	// Se conservan por compatibilidad con MediaStream: siempre por CPU.
	HRESULT SetD3DManager(IUnknown*, UINT, UINT) { return S_OK; }
	const bool HasD3DManager() const { return false; }

	HRESULT EnsureRenderTarget(UINT width, UINT height);
	HRESULT Generate(IMFSample* sample, REFGUID format, IMFSample** outSample);
	void Close();
};
