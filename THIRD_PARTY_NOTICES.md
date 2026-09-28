# Avisos de terceros

Martec Cámara incluye los siguientes componentes. Los textos completos de sus
licencias van en la carpeta `licencias/` del paquete instalado.

## Windows

| Componente | Licencia | Dónde se usa |
|---|---|---|
| Python | PSF License | Intérprete incluido |
| Tcl/Tk | Licencia Tcl (tipo BSD) | Ventana de la app |
| OpenCV (opencv-python-headless 4.14) | Apache-2.0 | Captura, detección de caras y procesamiento de imagen |
| Modelo YuNet `face_detection_yunet_2023mar.onnx` (Shiqi Yu, opencv_zoo) | MIT | Detección de caras, también de lado |
| Clasificadores Haar de OpenCV | Intel License Agreement (tipo BSD, en la cabecera de cada XML) | Detección de caras de respaldo |
| NumPy | BSD-3-Clause (y licencias permisivas de sus componentes) | Procesamiento |
| cv2_enumerate_cameras | MIT | Lista de cámaras por nombre |
| softcam (tshino) | MIT | Base del driver de cámara virtual "Martec Cámara" |
| DirectShow BaseClasses (Microsoft, incluidas en softcam) | MIT | Driver de cámara virtual |
| VCamSample (Simon Mourier) | MIT | Base del driver moderno de Windows 11 (Media Foundation) |
| C++/WinRT y WIL (Microsoft) | MIT | Encabezados usados por el driver moderno |
| PyInstaller (cargador) | GPL-2.0 con excepción de cargador | Empaquetado; la excepción permite distribuir con cualquier licencia |

En Windows no se incluye ningún componente GPL: la cámara virtual es un driver
propio basado en softcam, personalizado con el nombre "Martec Cámara".

## macOS

Lo mismo que en Windows salvo el driver (softcam es solo para Windows), más:

| Componente | Licencia | Dónde se usa |
|---|---|---|
| pyobjc | MIT | Lista de cámaras |
| pyvirtualcam | **GPL-2.0** | Solo en el programa aparte `puente-vcam` |

`pyvirtualcam` es GPL-2.0 y OpenCV es Apache-2.0; según la Free Software
Foundation no son compatibles dentro de un mismo programa. Por eso en Mac la
cámara virtual la maneja `puente-vcam`, un programa aparte que recibe los
cuadros por una tubería. Su código fuente y el de `pyvirtualcam` van en la
carpeta `fuentes/` del paquete, como pide la GPL.

En Mac **OBS Studio** (GPL-2.0) no se distribuye: cada usuario lo instala desde
https://obsproject.com y se usa solo su cámara virtual.
