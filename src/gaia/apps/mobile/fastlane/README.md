# iOS build lanes

fastlane lanes that build the GAIA phone app for iOS: an unsigned simulator build that
needs no account, and a development-signed build installed on your own iPhone over a
cable. Android comes later.

Every account value (team, App Store Connect key, certificate repository, bundle ids,
the phone) comes from environment variables. Nothing about one developer's account is
committed, and a lane stops at once with the name of each variable it is missing.

## Setup

Needs macOS with Xcode, Node and the app's `npm install`, and a Ruby with Bundler (for
example Homebrew's `ruby`). From `src/gaia/apps/mobile`:

```bash
bundle install          # gems go to vendor/bundle (.bundle/config), which git ignores
bundle exec fastlane lanes
```

The Gemfile pins `fastlane` and `cocoapods` exactly; `expo prebuild` runs this CocoaPods.
fastlane wants a UTF-8 locale: `export LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8`.

Keep your values in a file outside the repository, start it from
[`fastlane.env.example`](fastlane.env.example), and source it before a lane:

```bash
set -a; . /path/to/your/fastlane.env; set +a
```

Don't name it `fastlane/.env`: fastlane loads that file on its own, and it sits in the
repository.

## Lanes

| Lane | What it does |
|------|--------------|
| `bundle exec fastlane ios check` | `expo prebuild` with `APP_ENV=production`, then an unsigned Release build for the simulator. No credentials. |
| `bundle exec fastlane ios device` | Prebuild, match `development` signing, and a signed build for iPhone: an archive and an `.ipa`. |
| `bundle exec fastlane ios sideload` | Installs the last `ios device` build on `GAIA_IOS_DEVICE` with `xcrun devicectl device install app`. |

`ios device` and `ios sideload` take `app_env:production` (the default, Release, the
JavaScript bundled in) or `app_env:development` (Debug, `expo-dev-client`, JavaScript
from Metro). `ios device` also takes:

- `register_device:true` adds `GAIA_IOS_DEVICE` to the team's devices through the App
  Store Connect API before signing. The phone must be connected.
- `create_profiles:true` lets match create or renew the development certificate and
  profile, and add newly registered devices to it. Without it match only reads what the
  repository already holds.

The first run on a new phone or a new team is therefore:

```bash
bundle exec fastlane ios device create_profiles:true register_device:true
bundle exec fastlane ios sideload
```

and every later build is `ios device` followed by `ios sideload`.

## Environment variables

| Variable | What it is | Needed by |
|----------|------------|-----------|
| `GAIA_APPLE_TEAM_ID` | The 10-character Apple Developer team id to sign with | `ios device` |
| `GAIA_ASC_KEY_ID` | The App Store Connect API key id | `ios device` |
| `GAIA_ASC_ISSUER_ID` | The App Store Connect API issuer id (a UUID) | `ios device` |
| `GAIA_ASC_KEY_PATH` | Path to that key's `.p8` file (App Store Connect, Users and Access, Integrations) | `ios device` |
| `GAIA_MATCH_GIT_URL` | The private git repository match keeps the certificate and profiles in | `ios device` |
| `GAIA_MATCH_GIT_BRANCH` | Optional. That repository's branch; `main` when unset | `ios device` |
| `GAIA_MATCH_PASSWORD` | The passphrase that encrypts the match repository | `ios device` |
| `GAIA_IOS_BUNDLE_ID` | The production app's bundle id, as `app.config.ts` sets it for `APP_ENV=production` | `ios device` (`app_env:production`) |
| `GAIA_IOS_BUNDLE_ID_DEV` | The development app's bundle id, as `app.config.ts` sets it for `APP_ENV=development` | `ios device app_env:development` |
| `GAIA_IOS_DEVICE` | The iPhone's device id, from `xcrun devicectl list devices` | `ios sideload`, `ios device register_device:true` |
| `GAIA_MOBILE_APP_DIR` | Optional. The app folder to build; the folder above `fastlane/` when unset | every lane |
| `GAIA_IOS_BUILD_DIR` | Optional. Where builds go; `build/ios` in the app folder when unset (git ignores it) | every lane |
| `GAIA_BUILD_JOBS` | Optional. Passed to Xcode as `-jobs` to cap parallel compiles; Xcode decides when unset | `ios check`, `ios device` |

`ios device` checks the bundle id the generated project builds against
`GAIA_IOS_BUNDLE_ID` (or `_DEV`) and stops when they differ, so a profile is never made
for an app that isn't the one being built.

## How signing stays contained

- **The passphrase reaches match only.** Every lane takes `GAIA_MATCH_PASSWORD` (and
  any `MATCH_PASSWORD`) out of the environment as it starts; `ios device` sets
  `MATCH_PASSWORD` for match's call alone and removes it again. Xcode copies its
  environment into build settings and logs, so neither name ever reaches it.
- **A keychain per run.** The certificate goes into a temporary keychain made for the
  run and deleted when the run ends, never the login keychain.
- **Read-only by default.** match changes the repository or the Developer Portal only
  under `create_profiles:true`.
