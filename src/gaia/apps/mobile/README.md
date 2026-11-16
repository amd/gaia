# GAIA mobile

The native (Expo, React Native) phone app for the GAIA Agent UI. GAIA runs on your PC;
the app is a remote for it on your home network. It pairs with the PC by QR code and
then uses the same `/api/*` routes and SSE chat stream the web Agent UI does. For what
it does and how to pair, see the
[Phone App guide](https://amd-gaia.ai/docs/guides/mobile). This page is for building it.

```bash
npm install
npm run typecheck
npm run lint
npm test                                  # jest: ios, android and web projects
APP_ENV=development npm run export:web    # expo export --platform web
npm run check:tokens                      # design tokens match the generated file
```

To develop without pairing, start a development build with `EXPO_PUBLIC_GAIA_URL` set to a
GAIA backend you run yourself (`python -m gaia.ui.server`); the pairing screen is skipped.

Layout: `src/app` (thin routes), `src/features` (one folder per screen),
`src/api` (`GaiaClient`), `src/data` (TanStack Query), `src/state` (zustand),
`src/platform` (file trios: `x.types.ts`, `x.ts`, `x.web.ts`), `src/i18n`,
`src/ui` (owned by Design).

Strings, types and locale metadata are not copied: the app imports them from the
web Agent UI through the `@webui/*` alias (`../webui/src/*`), type-only for
`@webui/types` and the pure translator for `@webui/i18n/registry`. New app copy
goes into the webui catalogs under a `mobileApp.` key, in English and Arabic.

## Native builds

The app runs as a development build (`expo-dev-client`), not in Expo Go. Every
native module it uses is installed up front, so JS changes reach a built app over
Metro; only a change that moves the native fingerprint needs a new build.

- **`APP_ENV` is required** by every Expo command (`start`, `export`, `prebuild`,
  `install`, `expo-doctor`). `@expo/fingerprint` doesn't fail without it: it
  silently hashes without the config, so set it there too. `development` builds
  `ai.amd.gaia.mobile.dev` and `production` builds `ai.amd.gaia.mobile`; the URL
  scheme is the same id and stays fixed. To build for your own Apple team, set
  `GAIA_IOS_BUNDLE_ID` (production) and `GAIA_IOS_BUNDLE_ID_DEV` (development) to
  your App IDs; they change the iOS bundle id only, and fastlane checks the
  generated project against them.
- **`ios/` and `android/` are generated** by `npx expo prebuild` from
  `app.config.ts` and `plugins/`, and never committed.
- **Plain HTTP is for development only, and stays local:** iOS allows the local
  network, Android only `10.0.2.2` (the emulator's alias for this computer),
  `127.0.0.1` and `localhost`. On the Android emulator, reach Metro at `10.0.2.2`,
  or at `localhost` through `adb reverse`; a LAN address is refused. A production
  phone build never sends plain HTTP: it reaches a PC only by pairing, over TLS
  pinned to the PC's certificate, and ignores `EXPO_PUBLIC_GAIA_URL`. iOS keeps
  App Transport Security's local-network exception in every build, because the
  paired PC is a home-network address.
- **Android C++ compiles** run at most `GAIA_BUILD_JOBS` (default 4) at a
  time in each module.
- **iOS builds with fastlane:** `bundle exec fastlane ios check` (an unsigned
  simulator build), `ios device` and `ios sideload` (a signed build on your own
  iPhone). Every account value comes from environment variables; see
  [`fastlane/README.md`](fastlane/README.md).
- **The fingerprint a build must match:**
  `APP_ENV=development npx @expo/fingerprint fingerprint:generate . --platform ios`
  (or `android`). npm scripts and `.gitignore` don't count (`fingerprint.config.js`).
