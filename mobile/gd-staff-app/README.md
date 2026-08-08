# gd-staff-app (skeleton)

Skeleton listo para arrancar una app Android (Expo/React Native).

> Este skeleton no incluye `node_modules`. Se instala con `npm i` desde esta carpeta.

## Run (dev)

```bash
cd mobile/gd-staff-app
npm i
npm run start
```

## iPhone (staff) sin App Store: PWA

**PWA** (Progressive Web App) = una web que se “instala” (icono + pantalla completa) sin pasar por App Store.

- URL: `https://greendiamond.cl/crm/web/views/staff.html`
- iPhone/iPad (Safari): Compartir → **Agregar a pantalla de inicio**
- Android (Chrome): menú ⋮ → **Instalar app**
- Mac (Safari Sonoma+): **Archivo → Agregar al Dock** (o en Chrome/Edge el ícono “Instalar” en la barra URL)

> Nota: No existe “Expo Go para Mac”. En Mac se prueba con navegador (PWA) o con iOS Simulator (Xcode).

### iPhone (dev) — recomendado (Development Build, sin Expo Go)

Expo Go en iPhone hoy exige SDK nuevo. Para no depender de Expo Go, usamos **Development Build**.

Requisitos:
- macOS con **Xcode** instalado (App Store).
- iPhone conectado por cable (Trust this computer).
- Importante: en iOS **evita rutas con espacios** (ej: `CRM 2025`). Para compilar, copia este proyecto a una ruta tipo `~/dev/gd/gd-staff-app`.

Pasos:
```bash
cd mobile/gd-staff-app
npm i

# genera ios/ y android/ (native projects)
npm run prebuild

# build + instala en tu iPhone (te pedirá elegir device)
npx expo run:ios --device
```

En Xcode:
- Si te pide signing: selecciona tu Apple ID (Personal Team) para desarrollo (gratis, pero expira cada pocos días).
- Si `expo run:ios` dice *"No iOS devices available in Simulator.app"*: abre **Simulator.app** (Xcode) o instala un Simulator en Xcode → Settings → Platforms.

### iPhone (dev) — rápido (Expo Go)

- Instala **Expo Go** desde App Store.
- En Mac: `npm run start` y escanea el QR con la cámara del iPhone (abre en Expo Go).

### Mac (dev)

- Web (rápido): `npm run web` (abre en navegador).
- iOS Simulator (si tienes Xcode): `npm run ios`.

## Android (release APK, sin Play Store)

Requisitos:
- Android Studio instalado (para SDK/ADB) o al menos `adb` disponible.

Pasos:
```bash
cd mobile/gd-staff-app
npm i
npm run prebuild

cd android
./gradlew assembleRelease
```

APK generado:
- `mobile/gd-staff-app/android/app/build/outputs/apk/release/app-release.apk`

Instalación en Android (USB debug):
```bash
adb install -r android/app/build/outputs/apk/release/app-release.apk
```

## Variables

- Backend prod: `https://greendiamond.cl/crm`
- JWT: se obtiene desde `POST /auth/login` y se valida con `GET /me`
