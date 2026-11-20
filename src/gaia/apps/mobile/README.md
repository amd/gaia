# GAIA mobile

The phone app for GAIA, built with Expo and React Native (iOS first; Android can't pair yet).

## What it is

GAIA runs on your PC. The phone is a paired remote for it: it pairs once with a code from the
PC, then talks to the PC's `/api/*` routes and SSE chat stream, the same ones the web Agent UI
uses, over TLS pinned to that PC's certificate. It works on your home network.

The phone shows your chats, live, and lets you start and continue them, stop a reply, approve
permission requests, answer GAIA's questions and see its notifications. It shows the PC's scheduled
tasks and goals, read-only. Models, files, memory and keys stay on the PC, and anything that
changes the PC itself (setup, models, settings, documents, connectors) is done there. For pairing steps, what stays on the PC and user-facing troubleshooting, see
the [Phone App guide](https://amd-gaia.ai/docs/guides/mobile). This page is for building and
changing the app.

## Quick start

You need Node 22.13 or newer (what React Native 0.88 declares; the repo-root `.nvmrc` pins 20.20.0
for the web UI), Xcode with an iOS simulator, and
the Python environment that runs GAIA ([`docs/reference/dev.mdx`](../../../../docs/reference/dev.mdx)).
Ruby with Bundler is needed only for the fastlane lanes under [Native builds](#native-builds).

```bash
cd src/gaia/apps/mobile
npm ci
APP_ENV=development npx expo run:ios      # first time: generates ios/ and builds a dev client
APP_ENV=development npx expo start --dev-client
```

Every Expo command needs `APP_ENV`; see [Native builds](#native-builds).

**Against a backend you start yourself, without pairing.** Start the Agent UI server
(`python -m gaia.ui.server`, port 4200 by default) and give Metro its address:

```bash
EXPO_PUBLIC_GAIA_URL=http://localhost:4200 APP_ENV=development npx expo start --dev-client
```

A development build with `EXPO_PUBLIC_GAIA_URL` set is already connected over plain HTTP, so it
skips the pairing screens. Native production builds ignore the variable; a web export reads it.

**Paired with a real PC.** Leave `EXPO_PUBLIC_GAIA_URL` unset so the app shows the pairing screens
and uses the pinned transport. On the PC, turn on phone access as the guide describes (the
`device_pairing` setting in `~/.gaia/config.json`, then Settings › Phones and tablets › Allow
paired devices to connect). A simulator has no usable camera: choose **Pair a phone or tablet** on
the PC, use **Copy pairing link**, paste it into the app's paste field, and choose **Allow** on the
PC. The simulator must reach the PC's home-network address, so run both on the same network.

## How it works

- **Routes vs features.** `src/app` holds thin Expo Router routes. Each screen's logic lives in
  `src/features/<name>`.
- **API and types.** `src/api` has `GaiaClient` (`client.types.ts`, `http.ts`, `sse.ts`) and the
  endpoint table (`constants.ts`). Shared code is not copied: the app imports it from the web
  Agent UI through the `@webui/*` alias (`../webui/src/*`): types (type-only) and the translator
  and locale registry. API shapes come from the generated `webui/src/types/api.gen.ts`.
- **State.** `src/data` is TanStack Query (queries, mutations, the chat stream). `src/state` is
  zustand (the connection, notifications, locale, per-chat stream state). `src/platform` holds
  per-platform code as file trios: `x.types.ts`, `x.ts`, `x.web.ts`. `src/ui` is the component
  library and design tokens.
- **Pairing and the pinned transport.** `src/features/pairing` is the flow
  (`PairingFlow.tsx`, `pinnedPairingService.ts`; `fakePairingService.ts` serves dev builds
  and the web export). The native module `modules/gaia-transport` (Swift, `ios/`) opens every request
  to a paired PC with its certificate pin and keeps the device token in the Keychain, so
  JavaScript never holds it. A pin that no longer matches shows `PinMismatchScreen`. The PC
  must also **Allow** each new phone before it can reach any chat.
- **Route classes.** A paired phone reaches only the routes the PC's device allowlist
  (`src/gaia/ui/devices/routes.py`) lists. `src/api/routeAccess.gen.ts` is generated from the PC's
  route guards (`python -m gaia.ui.export_route_access` from the repo root) and classifies each
  route as `pc`, `remote` and so on; do not edit it. `api/constants.test.ts` fails if the app
  calls a route a paired phone can't reach.
- **Multiple PCs.** `pairedPcs.ts` and `PairedPcsScreen.tsx` keep several paired PCs. A pairing
  code may name home-network addresses only; the rules are in `homeNetwork.ts`, with a Swift twin
  in `HomeNetwork.swift`.

## Strings, Arabic and design rules

New copy goes in the web Agent UI's catalogs, `../webui/src/i18n/locales/en.ts` and `ar.ts`, under a
`mobileApp.` key, in both languages in the same change. Never edit a desktop key for the phone. Copy
speaks from the phone: say "your PC", not "this PC". Use `useT`
(`src/i18n/useT.ts`). Arabic shows the English in brackets as the desktop does, so compact controls
(chips, tabs, badges) and text-field placeholders use `t.bare(key)`, and their accessibility label
keeps the full `t(key)`. Layout is right-to-left in Arabic, so use logical properties and keep
server-sent English text in an `ltr` container.

Design conventions:

- Content never touches the screen sides: use `paddingX="gutter"` (`space.gutter`, 16 pt). `Screen`
  pads its own footer.
- Every standalone action is a real `Button` with a border or fill, pressed and disabled states and
  a target of at least 44 pt. Plain text taps are for links inside running text and breadcrumbs.
- No colored side rails on cards, notices or banners; show severity with an icon and a tint or a
  hairline border.
- Indent only what a disclosure reveals; content at the same level aligns to the gutter.
- Use the tokens in `src/ui/tokens` (generated from the web UI's CSS by `npm run gen:tokens`), never
  new hex values.
- Check screenshots in dark mode as well as light, in English and Arabic.

## Testing

```bash
npm run typecheck
npm run lint
npm test                                  # jest: ios, android and web projects
npm run check:tokens                      # design tokens match the generated file
APP_ENV=development npm run export:web    # expo export --platform web
```

- **Jest** runs each test under the `ios`, `android` and `web` presets (`jest.config.js`). Tests
  that need native components are named `*.native.test.tsx`. Run jest without `APP_ENV` set, as CI
  does: a test that imports a module reading the app config must mock `expo-constants`.
- **Don't put tests under `src/app/`.** Expo Router bundles everything there. Scene and route
  tests live beside their feature.
- **A fake PC for tests.** `src/api/fake.ts` is an in-memory `GaiaClient` for tests only; the app
  never imports it.
- **Pairing changes need the real start path.** Check any pairing change against a backend started
  the normal way (`python -m gaia.ui.server`, with phone access on), not only the test harness. The
  harness once hid a real listener bug.

## Native builds

The app runs as a development build (`expo-dev-client`), not in Expo Go. Every native module it
uses is installed up front, so JS changes reach a built app over Metro; only a change that moves
the native fingerprint needs a new build.

- **`APP_ENV` is required** by every Expo command (`start`, `export`, `prebuild`, `install`,
  `expo-doctor`). `@expo/fingerprint` doesn't fail without it: it silently hashes without the
  config, so set it there too. `development` builds `ai.amd.gaia.mobile.dev` and `production`
  builds `ai.amd.gaia.mobile`; the URL scheme is the same id and stays fixed. To build for your own
  Apple team, set `GAIA_IOS_BUNDLE_ID` (production) and `GAIA_IOS_BUNDLE_ID_DEV` (development) to
  your App IDs; they change the iOS bundle id only, and fastlane checks the generated project
  against them.
- **`ios/` and `android/` are generated** by `npx expo prebuild` from `app.config.ts` and
  `plugins/`, and never committed.
- **Plain HTTP is for development only, and stays local:** iOS allows the local network, Android
  only `10.0.2.2` (the emulator's alias for this computer), `127.0.0.1` and `localhost`. On the
  Android emulator, reach Metro at `10.0.2.2`, or at `localhost` through `adb reverse`; a LAN
  address is refused. A production phone build never sends plain HTTP: it reaches a PC only by
  pairing, over TLS pinned to the PC's certificate, and ignores `EXPO_PUBLIC_GAIA_URL`. iOS keeps
  App Transport Security's local-network exception in every build, because the paired PC is a
  home-network address.
- **Android C++ compiles** run at most `GAIA_BUILD_JOBS` (default 4) at a time in each module.
- **iOS builds with fastlane:** `bundle exec fastlane ios check` (an unsigned simulator build),
  `ios device` and `ios sideload` (a signed build on your own iPhone). Every account value comes
  from environment variables; see [`fastlane/README.md`](fastlane/README.md).
- **The fingerprint a build must match:**
  `APP_ENV=development npx @expo/fingerprint fingerprint:generate . --platform ios` (or
  `android`). npm scripts and `.gitignore` don't count (`fingerprint.config.js`).

## Troubleshooting

- **"APP_ENV must be "development" or "production"".** Set `APP_ENV` on the command, for example
  `APP_ENV=development npx expo start`. `app.config.ts` refuses to load without it.
- **"Can't reach your PC".** Phone access must be on, and the phone or simulator must be on the
  same home network as the PC. Then show a new pairing code.
- **A cleartext request is refused.** Plain HTTP works only in a development build, and on Android
  only to `10.0.2.2`, `localhost` and `127.0.0.1`. Use the pairing flow, or
  `EXPO_PUBLIC_GAIA_URL` in development.
- **Pairing screens don't appear in a dev build.** `EXPO_PUBLIC_GAIA_URL` is set; unset it to use
  pairing.
- **The installed dev client ignores a new native module or plugin.** The native fingerprint
  changed. Compare it as above and rebuild with `npx expo run:ios`; Metro can't deliver native
  changes.
- **Android can't pair.** The pinned transport is iOS only.
