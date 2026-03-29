# GD Staff App (Android APK) + iOS PWA

Objetivo: app descargable para **todo el staff** sin pagar stores.

- **Android**: React Native (Expo) → generar **APK** y distribuir directo (sideload/MDM/WhatsApp interno).
- **iPhone/iPad**: usar la **PWA** del CRM (Chat y módulos web) → “Agregar a pantalla de inicio”.

> Nota: publicar en App Store/Google Play siempre tiene costo de cuenta (Apple anual / Google one‑time). Este plan evita stores.

## Módulos objetivo

1) Chat interno (threads + mensajes + adjuntos)
2) Notificaciones internas (system_notifs + badges)
3) Eventos (calendario/agenda, confirmados por semana, faltantes)
4) Operadores (recetas/mice/operación) en UX móvil

## Backend ya disponible

- Auth JWT: `POST /crm/auth/login`, `GET /crm/me`
- Chat:
  - `GET /crm/chat/threads`
  - `GET /crm/chat/threads/{id_thread}/messages`
  - `POST /crm/chat/threads/{id_thread}/send`
  - `GET /crm/chat/users?include_staff=1` (incluye operadores/choferes)
- Notificaciones internas:
  - `GET /crm/notifications/summary`
  - `GET /crm/notifications/system_notifs`

## Roadmap recomendado

### Fase 1 (rápida): Android APK con Expo

Requisitos del PC del dev:
- Node 18+
- Android Studio (SDK + adb)

Pasos:
1) Crear app (en una carpeta fuera del repo o dentro de `mobile/`):
   - `npx create-expo-app gd-staff-app --template blank-typescript`
2) Instalar libs mínimas:
   - `npm i @react-navigation/native @react-navigation/native-stack`
   - `npx expo install react-native-screens react-native-safe-area-context`
   - `npx expo install expo-secure-store`
3) Implementar:
   - Login (JWT) guardado en SecureStore
   - Refresh: validar con `/crm/me`; si 401 → login
   - Chat: threads + messages
4) Build APK:
   - Opción A (local, sin pagar): `npx expo prebuild` → `android/gradlew assembleRelease`
   - Opción B (cloud): EAS Build (requiere cuenta Expo; tiene tier gratis limitado)

Distribución:
- Compartir `app-release.apk` al staff (MDM, Drive, etc). Android permite instalar apps fuera del store si habilitas “orígenes desconocidos”.

### Fase 2: iOS PWA

Usar `https://greendiamond.cl/crm` y el módulo chat web:
- iOS Safari: Compartir → “Agregar a pantalla de inicio”.

## Seguridad (token)

RN no es “más seguro” por sí solo. La seguridad depende de:
- expiración de JWT (backend)
- almacenamiento seguro (Android Keystore via `expo-secure-store`)
- invalidación/refresh (revalidar `/crm/me`)

## Qué necesito de ti

- Nombre final de la app (pantalla inicio): ej. “GD Staff”
- Ícono (512x512)
- Lista de roles que deben ver Chat (por ahora backend permite todos; si quieres restringir, lo ajustamos por rol)
