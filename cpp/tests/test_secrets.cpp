// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Same cases as tests/unit/test_memory_keeps_no_secrets.py, so both runtimes
// agree on what never reaches memory.

#include <gtest/gtest.h>
#include <gaia/secrets.h>

#include <string>
#include <utility>
#include <vector>

using namespace gaia;

namespace {

const std::vector<std::string> kSecrets = {
    "My bank PIN is 4821",
    "pin: 9921",
    "The CVV is 123",
    "my passcode is 778899",
    "My social security number is 123-45-6789",
    "card 4111 1111 1111 1111 exp 12/29",
    "online banking password is Blue-Heron-77",
    "The user's PIN for Chase is 4821",
    "User's PIN number for the ATM is 4821",
    "my pin was 4821",
    "Bank PIN \xE2\x80\x94 4821",
    "User's SSN is 123 45 6789",
    "User's social security number is 123456789",
    "4111\xE2\x80\x93" "1111\xE2\x80\x93" "1111\xE2\x80\x93" "1111",
    "4111.1111.1111.1111",
    "4111  1111  1111  1111",
    "card4111111111111111",
    "Amex 3782 822463 10005",
    "Card: 4111 1111 1111 1111 12/29 123",
    "4111111111111111 12/29",
    "call 5 4111 1111 1111 1111",
    "Amex 3782 822463 10005 0425",
    "sk-ant-api03-abcdefghijklmnop",
    "export OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456",
    "token ghp_abcdefghijklmnopqrstuvwxyz0123",
    "AKIAABCDEFGHIJKLMNOP",
    "Authorization: Bearer abcdefghijklmnopqrstuvwxyz.123",
    "the api key: hunter2",
    "HALIBUT-4417-ZULU",
};

const std::vector<std::string> kPinForms = {
    "User's PIN for their Chase debit card is 4821", "I changed my PIN to 4821", "PIN 4821",
    "My Chase card PIN is 4821", "Chase bank PIN: 4821", "My PIN is 4821", "PIN number 4821",
    "PIN code: 4821", "PIN code 4821", "PIN of my card is 4821", "PIN for my card 4821",
    "PIN for Chase 4821", "PIN on card 4821", "pin on chase 4821", "PIN at my bank 4821",
    "PIN of my chase card 4821", "PIN is 4821", "4821 is my PIN", "pin#4821", "PIN (4821)",
    "PIN number on my card 4821", "PIN number for Chase is 4821", "CVV for my Visa is 123",
    "CVV on my card: 123", "cvc for amex 1234", "security code on the back is 123",
    "passcode for the alarm is 4821", "Passcode number 4821", "pass code is 4821",
    "PIN code for Chase is 4821", "PIN code on my card: 4821", "PIN code at my bank 4821",
    "PIN code of my card is 4821", "PIN code is 48219", "PIN code is 4821937", "PIN code is 123456",
    "My PIN code: 123456", "PIN code for my Chase card is 123456", "My PIN code is 560001",
    "PIN code 560001", "PIN code for Chase is 123456", "PIN code of HDFC Bank: 482193",
    "PIN code for Wells Fargo 482193", "PIN code of Bangalore is 560001",
};

const std::vector<std::string> kNotSecrets = {
    "Call me at 555-123-4567",
    "Meeting on 2026-10-23 at 3pm",
    "Connect the wire to pin 3",
    "I pinned 12 tabs",
    "Order 1234567890123 shipped",
    "Tracking 1000000000009 is out for delivery",
    "Logged at 9000000000001 ms",
    "My zip code is 97201",
    "My phone's IMEI is 356938035643809",
    "IMEI 490154203237518",
    "Call 1-800-555-0199 or +1 (408) 555 0123",
    "See pages 4111-4122",
    "the security code review is 2026 Q4",
    "pin the tab to slot 2",
    "408-555-0123 408-555-0199",
    "IMEI 356938035643809 12",
    "IMEI 356938035643809 12/29",
    "GPIO pin 1023",
    "Pin on page 123",
    "Pin code on page 123",
    "+49 30 12345678 or +49 89 98765432",
    "9400 1000 0000 0000 0000 00",
    "4111 1111 1111 1112",  // fails the Luhn check
};

// Card numbers spaced with every kind of horizontal whitespace still count.
const std::vector<std::string> kSpacedCards = {
    "4111 - 1111 - 1111 - 1111",
    "4111   1111   1111   1111",
    "4111\t1111\t1111\t1111",
    "4111\xE2\x80\xAF" "1111\xE2\x80\xAF" "1111\xE2\x80\xAF" "1111",  // U+202F
    "4111\xE2\x80\x89" "1111\xE2\x80\x89" "1111\xE2\x80\x89" "1111",  // U+2009
    "4111\xE3\x80\x80" "1111\xE3\x80\x80" "1111\xE3\x80\x80" "1111",  // U+3000
    "4111\xC2\xA0" "1111\xC2\xA0" "1111\xC2\xA0" "1111",              // U+00A0
};

}  // namespace

TEST(SecretsTest, FlagsEveryKnownSecretShape) {
    for (const auto& s : kSecrets) EXPECT_TRUE(looksLikeCredential(s)) << s;
}

TEST(SecretsTest, FlagsEveryPinAndSecurityCodeForm) {
    for (const auto& s : kPinForms) EXPECT_TRUE(looksLikeCredential(s)) << s;
}

TEST(SecretsTest, LeavesOrdinaryNumbersAlone) {
    for (const auto& s : kNotSecrets) EXPECT_FALSE(looksLikeCredential(s)) << s;
}

TEST(SecretsTest, CardsSpacedWithUnicodeWhitespaceCount) {
    for (const auto& s : kSpacedCards) EXPECT_TRUE(looksLikeCredential(s)) << s;
}

TEST(SecretsTest, LineBreaksNeverJoinACardNumber) {
    for (const std::string nl : {"\n", "\r\n", "\xE2\x80\xA8", "\xC2\x85"}) {
        const std::string text = "4111 1111" + nl + "1111 1111";
        EXPECT_FALSE(looksLikeCredential(text)) << "separator bytes: " << nl.size();
    }
}

TEST(SecretsTest, MaskReplacesOnlyTheSecretSpans) {
    const std::string masked =
        maskCredentials("My bank PIN is 4821 and my card is 4111 1111 1111 1111. Remember them.");
    EXPECT_EQ(masked.find("4821"), std::string::npos);
    EXPECT_EQ(masked.find("4111"), std::string::npos);
    EXPECT_NE(masked.find("[secret removed]"), std::string::npos);
    EXPECT_NE(masked.find("Remember them."), std::string::npos);
    EXPECT_EQ(maskCredentials("nothing to hide here"), "nothing to hide here");
}

TEST(SecretsTest, LongInputStaysFastAndCorrect) {
    std::string big(200000, 'a');
    big += " password is swordfish ";
    big += std::string(200000, ' ');
    const auto spans = credentialSpans(big);
    ASSERT_EQ(spans.size(), 1u);
    EXPECT_EQ(big.substr(spans[0].first, spans[0].second - spans[0].first), "password is swordfish");
}

TEST(CredentialSpansTest, KeyCrossingTheScanWindowIsMaskedWhole) {
    // The token starts before the second window's start and ends past the first window's end.
    const std::string token = "sk-" + std::string(500, 'A');
    const std::string text = std::string(3699, 'x') + " " + token + " tail";
    const std::string masked = maskCredentials(text);
    EXPECT_EQ(masked.find("AAAA"), std::string::npos);
    EXPECT_NE(masked.find("[secret removed] tail"), std::string::npos);
}

TEST(SecretsTest, FlagsAssignmentsToUnderscoreJoinedNames) {
    for (const std::string s : {"DB_PASSWORD=Blue-Heron-77", "aws_secret_access_key = wJalrXUtnFEMIbPxRfiCYzEXAMPLE",
                                "GITHUB_TOKEN=abcdef0123456789", "export STRIPE_SECRET_KEY: sk_live_abcdef"}) {
        EXPECT_TRUE(looksLikeCredential(s)) << s;
    }
    // The character before the name is kept; the name and its value go.
    EXPECT_EQ(maskCredentials("run with DB_PASSWORD=hunter2 today"), "run with [secret removed] today");
    EXPECT_EQ(maskCredentials("my_password: hunter2 ok"), "my_[secret removed] ok");
    for (const std::string s : {"MAX_TOKENS=4096", "the tokens = words", "compassword=1"}) {
        EXPECT_FALSE(looksLikeCredential(s)) << s;
    }
}

TEST(SecretsTest, SettingsNamedAfterASecretAreNotCredentials) {
    for (const std::string s : {"PASSWORD_MIN_LENGTH=12", "TOKEN_LIMIT: 4096", "MAX_TOKEN=4096",
                                "SECRET_ROTATION_ENABLED=true", "API_KEY_REQUIRED: false",
                                "TOKEN_TTL=3600s", "set PASSWORD_MIN_LENGTH=12 and TOKEN_LIMIT: 4096, then retry"}) {
        EXPECT_FALSE(looksLikeCredential(s)) << s;
        EXPECT_EQ(maskCredentials(s), s);
    }
    for (const std::string s : {"DB_PASSWORD=hunter2xyz", "aws_secret_access_key = wJalrXUtnFEMIbPxRfiCYzEXAMPLE",
                                "GITHUB_TOKEN=abc123def456ghi789", "GITHUB_TOKEN_PROD=abc123def456ghi789",
                                "API_KEY_BACKUP: 1234567890abcdef"}) {
        EXPECT_TRUE(looksLikeCredential(s)) << s;
    }
}

TEST(SecretsTest, FlagsJwtsAndPrivateKeyBlocks) {
    EXPECT_TRUE(looksLikeCredential(
        "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV"));
    const std::string pem =
        "deploy key:\n-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\nq7x9\n-----END RSA PRIVATE KEY-----\nafter";
    EXPECT_EQ(maskCredentials(pem), "deploy key:\n[secret removed]\nafter");
    EXPECT_TRUE(looksLikeCredential("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk"));
    EXPECT_FALSE(looksLikeCredential("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"));
}

namespace {

// Assembled at run time so secret scanners don't flag the fixtures.
const std::string kGhp = std::string("ghp_") + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4";
const std::string kPat = std::string("github_pat_") + "11ABCDEFG0123456789_abcdefghijklmnop";
const std::string kGlpat = std::string("glpat-") + "abcdefghij0123456789";
const std::string kAkia = std::string("AKIA") + "ABCDEFGHIJKLMNOP";
const std::string kDbPw = std::string("DB_PASSWORD=") + "hunter2";
const std::string kApiSecret = std::string("API_SECRET=") + "s3cr3t";
const std::string kPem = std::string("-----BEGIN PRIVATE") + " KEY-----";
const std::string kRemote = "https://user:" + kGhp + "@github.com/amd/gaia.git";
const std::string kRedactedMark = "[redacted]";
const std::string kHiddenMark = "[credential file content hidden]";

std::string upperBody(const std::string& token, std::size_t prefix) {
    std::string out = token;
    for (std::size_t i = prefix; i < out.size(); ++i) {
        if (out[i] >= 'a' && out[i] <= 'z') out[i] = static_cast<char>(out[i] - 'a' + 'A');
    }
    return out;
}

bool has(const std::string& text, const std::string& part) { return text.find(part) != std::string::npos; }

}  // namespace

TEST(RedactCredentialsTest, MasksTokensUrlUserinfoAndSecretQueryValues) {
    const std::vector<std::pair<std::string, std::string>> cases = {
        {"origin\thttps://user:" + kGhp + "@github.com/amd/gaia.git (fetch)", kGhp},
        {"origin\thttps://x-access-token:" + kPat + "@github.com/a/b (push)", kPat},
        {"origin https://" + kGhp + "@github.com/a/b.git", kGhp},
        {std::string("remote ssh://deploy:") + "hunter2pass@git.example.com:2222/r.git", "hunter2pass"},
        {"GET https://api.example.com/v1/items?access_token=" + kGlpat + "&page=2", kGlpat},
        {std::string("GET https://api.example.com/v1/items?page=2&TOKEN=") + "s3cr3tvalue1", "s3cr3tvalue1"},
        {std::string("HTTPS://User:") + "Pa55w0rdXYZ@Example.COM/x", "Pa55w0rdXYZ"},
        {"export AWS_ACCESS_KEY_ID=" + kAkia, kAkia},
        {"token: " + upperBody(kGhp, 4), upperBody(kGhp, 4).substr(4)},
        {"GITLAB=" + upperBody(kGlpat, 6), upperBody(kGlpat, 6).substr(6)},
        {"pat=" + kPat, kPat},
        {std::string("slack ") + "xoxb-" + "123456789012-abcdefABCDEF", "123456789012-abcdefABCDEF"},
        {std::string("maps ") + "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2", "SyA1b2C3d4E5f6G7h8I9j0K1l2"},
        {std::string("key ") + "sk-ant-" + "api03-abcdefghij", "api03-abcdefghij"},
    };
    for (const auto& [text, secret] : cases) {
        const std::string out = redactCredentials(text);
        EXPECT_FALSE(has(out, secret)) << text;
        EXPECT_TRUE(has(out, kRedactedMark)) << text;
    }
    EXPECT_EQ(redactCredentials("origin\thttps://user:" + kGhp + "@github.com/amd/gaia.git (fetch)"),
              "origin\thttps://[redacted]@github.com/amd/gaia.git (fetch)");
    EXPECT_EQ(redactCredentials("GET https://h/x?access_token=" + kGlpat + "&page=2"),
              "GET https://h/x?access_token=[redacted]&page=2");
}

TEST(RedactCredentialsTest, LeavesOrdinaryTextAlone) {
    const std::vector<std::string> ordinary = {
        "origin\tgit@github.com:amd/gaia.git (fetch)",
        "Author: Jane Doe <jane.doe@example.com>",
        "see https://github.com/amd/gaia/pull/12 for details",
        "https://www.npmjs.com/package/@scope/pkg",
        "GET https://api.example.com/v1/items?page=2&sort=desc",
        "max_tokens=512 tokens_per_second=31.2",
        "ghp_short",
        "mailto:someone@example.com",
        std::string("task-sk-") + "abcdefghijklmnopqrstuvwxyz0123",
        std::string("build_sk-") + "abcdefghijklmnopqrstuvwxyz0123",
        "gaia version 0.25.4-rc16 (build 2026.11.16)",
        "commit 3f2a9c1e8b7d6a5f4e3d2c1b0a9f8e7d6c5b4a39 (HEAD -> main, origin/main)",
        "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
        "22b5ebb4b fix(cpp): do not run tool-call text quoted from a file",
        "AKIAABCDEFGHIJKLMNOPQ is one character too long for an access key id",
        "",
    };
    for (const auto& text : ordinary) EXPECT_EQ(redactCredentials(text), text) << text;
}

TEST(RedactCredentialsTest, LongInputIsScannedWhole) {
    std::string big(600000, 'a');
    big += " https://user:" + kGhp + "@github.com/a/b ";
    big += std::string(600000, '/');
    big += " " + kAkia;
    const std::string out = redactCredentials(big);
    EXPECT_FALSE(has(out, kGhp));
    EXPECT_FALSE(has(out, kAkia));
    EXPECT_EQ(redactCredentials(std::string(600000, ':') + "//"), std::string(600000, ':') + "//");
}

TEST(RedactToolOutputTest, NestedResultsAreMaskedAndKeepTheirShape) {
    const json result = {{"status", "success"},
                         {"stdout", "https://u:" + kGhp + "@github.com/a/b"},
                         {"steps", json::array({{{"output", "key " + kAkia}}})},
                         {"return_code", 0}};
    const json out = redactToolOutput(result);
    const std::string dumped = out.dump();
    EXPECT_FALSE(has(dumped, kGhp));
    EXPECT_FALSE(has(dumped, kAkia));
    EXPECT_EQ(out["return_code"], 0);
    EXPECT_EQ(out["status"], "success");
    EXPECT_EQ(out["steps"][0]["output"], "key [redacted]");
    EXPECT_EQ(redactToolOutput(json("clone " + kRemote)), json("clone https://[redacted]@github.com/amd/gaia.git"));
}

TEST(CredentialFileLinesTest, ALineFromACredentialFileIsHidden) {
    const std::vector<std::string> lines = {
        "./.env:3:" + kDbPw,
        ".env:" + kDbPw,
        "sub/dir/.env.production:1:" + kDbPw,
        "./config/.env.local-4-" + kApiSecret,
        "certs/server.key:1:" + kPem,
        "certs/server.pem:" + kPem,
        "deploy/tls/site.KEY:" + kPem,
        "proj\\secrets.json:2:  \"token\": \"abc\"",
        "> proj\\.env:1:" + kDbPw,
        "  proj\\.env:2:" + kApiSecret,
        std::string("./.env") + '\0' + kDbPw,
        "3:./.env:" + kDbPw,
        "3:.env:" + kDbPw,
        "\x1b[35m./.env\x1b[m\x1b[36m:\x1b[m3:" + kDbPw,
        "C:\\work\\app\\.npmrc:1://registry/:_authToken=x",
    };
    for (const auto& line : lines) {
        for (const std::string ending : {"\n", "\r\n"}) {
            const std::string out = maskCredentialFileLines(line + ending);
            for (const std::string secret : {"hunter2", "s3cr3t", "\"abc\"", "PRIVATE KEY", "_authToken"}) {
                EXPECT_FALSE(has(out, secret)) << line;
            }
            EXPECT_TRUE(has(out, kHiddenMark)) << line;
            ASSERT_GE(out.size(), ending.size());
            EXPECT_EQ(out.substr(out.size() - ending.size()), ending) << line;
        }
    }
    EXPECT_EQ(maskCredentialFileLines("./.env:3:" + kDbPw + "\n"), "./.env:" + kHiddenMark + "\n");
    EXPECT_EQ(maskCredentialFileLines("./config/.env.local-4-" + kApiSecret), "./config/.env.local-" + kHiddenMark);
}

TEST(CredentialFileLinesTest, OtherLinesAreLeftAlone) {
    const std::vector<std::string> lines = {
        "./src/app.py:12:# TODO: retry",
        "./.env.example:1:DB_PASSWORD=",
        "docs/env.md:3:put DB_PASSWORD in .env",
        ".gitignore:1:.env",
        "./README.md:7:copy .env.example to .env",
        "src/settings.py-40-load_dotenv('.env')",
        " M .env",
        "ls: cannot access '.env': No such file or directory",
        "src/keys.py:3:PUBLIC_KEY_PATH = 'certs/server.key'",
        "text-shadow: 0 1px 2px #000",
        "",
    };
    for (const auto& line : lines) EXPECT_EQ(maskCredentialFileLines(line), line) << line;
}

TEST(CredentialFileLinesTest, ASearchKeepsItsOtherLines) {
    const std::string grep = "./src/app.py:12:# TODO: retry\n./.env:3:" + kDbPw + "\n./config/.env.local-4-" +
                             kApiSecret + "\n./README.md:7:TODO list\n";
    const std::string out = maskCredentialFileLines(grep);
    EXPECT_FALSE(has(out, "hunter2"));
    EXPECT_FALSE(has(out, "s3cr3t"));
    EXPECT_TRUE(has(out, "./src/app.py:12:# TODO: retry\n"));
    EXPECT_TRUE(has(out, "./README.md:7:TODO list\n"));
    std::size_t hidden = 0;
    for (std::size_t at = out.find(kHiddenMark); at != std::string::npos; at = out.find(kHiddenMark, at + 1)) ++hidden;
    EXPECT_EQ(hidden, 2u);
    EXPECT_EQ(redactToolOutput(json{{"stdout", grep}})["stdout"], out);
}

TEST(CredentialFileNameTest, MatchesTheRefusedNamesAndExtensions) {
    for (const std::string name : {".env", ".ENV", ".env.local", ".env.production", ".env.development",
                                   "credentials.json", "service_account.json", "secrets.json", "id_rsa",
                                   "id_ed25519", "id_ecdsa", "id_dsa", "authorized_keys", "known_hosts", "shadow",
                                   "passwd", "sudoers", "htpasswd", ".netrc", ".pgpass", ".my.cnf", "wallet.dat",
                                   "keystore.jks", ".npmrc", ".pypirc", "server.pem", "site.KEY", "ca.crt",
                                   "ca.cer", "id.p12", "id.pfx", "app.jks", "debug.keystore"}) {
        EXPECT_TRUE(isCredentialFileName(name)) << name;
    }
    for (const std::string name : {".env.example", "env.md", "README.md", "keys.py", ".key", "pem", "passwd.txt",
                                   "id_rsa.pub", "package.json", ""}) {
        EXPECT_FALSE(isCredentialFileName(name)) << name;
    }
}

TEST(RedactFileTextTest, SecretShapedFileTextIsNotMasked) {
    const std::vector<std::string> notSecrets = {
        "\"repository\": \"git+ssh://git@github.com/amd/gaia.git\"",
        "postgresql://postgres@localhost:5432/app",
        "requests.get(f\"https://api.x.com/v1?key={api_key}\")",
        "# sk-learn-compatible-estimator-wrapper",
        "https://user:${GIT_TOKEN}@github.com/amd/gaia.git",
        std::string("https://user:") + "password@example.com/x",
        "curl https://api.example.com/v1?token=YOUR_TOKEN_HERE",
        "url = https://example.com/x?api_key=%(key)s",
        "see https://example.com/items?key=abc",
        std::string("https://user:") + "MYPASSWORD@example.com/x",
        "https://user:$GIT_TOKEN@github.com/a.git",
        "https://user:<token>@github.com/a.git",
        "https://example.com/x?token={{ api_token }}",
        "https://example.com/x?access_token=YOUR_ACCESS_TOKEN",
        "fetch(`https://x.com/?api_key=${apiKey}`)",
        "requests.get(f'https://x.com/?token={token}')",
        "https://user:${DB_PASSWORD}@db/app",
        "[server]\nhost = 127.0.0.1\nport = 8080\ndocs = https://amd-gaia.ai/docs/guides/chat\n"
        "remote = git@github.com:amd/gaia.git\n.env: listed here is not a search prefix\n",
    };
    for (const auto& line : notSecrets) {
        EXPECT_EQ(redactFileText(line), line) << line;
        EXPECT_FALSE(holdsCredentials(line)) << line;
    }
}

TEST(RedactFileTextTest, RealSecretsInFileTextAreStillMasked) {
    const std::string skProj = std::string("sk-proj-") + "Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4z";
    const std::string skAnt = std::string("sk-ant-api03-") + "Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4z";
    const std::string hexToken = "0123456789abcdef0123456789abcdef01234567";
    const std::vector<std::pair<std::string, std::string>> cases = {
        {"url = " + kRemote, kGhp},
        {std::string("postgresql://app:") + "hunter2@db.internal:5432/app", "hunter2"},
        {"https://x-access-token:" + kGlpat + "@gitlab.com/a/b.git", kGlpat},
        {"https://" + hexToken + "@github.com/amd/gaia.git", hexToken},
        {std::string("GET https://api.example.com/v1?access_token=") + "Zq81mXv0Lp7c", "Zq81mXv0Lp7c"},
        {"OPENAI_API_KEY=" + skProj, skProj},
        {"ANTHROPIC_API_KEY=" + skAnt, skAnt},
        {std::string("OLD_KEY=sk-") + "Ab3dEf6hIj9kLm2nOp5qRs8t", "Ab3dEf6hIj9kLm2nOp5qRs8t"},
        {std::string("postgresql://app:") + "p%40ssW0rd2024@db/app", "p%40ssW0rd2024"},
        {std::string("postgresql://app:") + "Pa$$w0rd2024@db/app", "Pa$$w0rd2024"},
        {std::string("postgresql://app:") + "{x}Pa55word@db/app", "Pa55word"},
        {std::string("postgresql://app:") + "HUNTERPASS@db/app", "HUNTERPASS"},
        {std::string("mysql://root:") + "$ecretP4ss@db/app", "$ecretP4ss"},
        {std::string("mysql://root:") + "${mixed_Case9}@db/app", "mixed_Case9"},
        {std::string("https://acct.blob.core.windows.net/c/b?sv=2022-11-02&sp=r") + "&sig=" +
             "Xk2bLq9vPz4Rt7Yw1Mn5Cd8Ef3Gh6Jk0%2BaBcD%2FeFgH%3D%3D",
         "Xk2bLq9vPz4Rt7Yw1Mn5Cd8Ef3Gh6Jk0"},
    };
    for (const auto& [line, secret] : cases) {
        const std::string out = redactFileText(line);
        EXPECT_FALSE(has(out, secret)) << line;
        EXPECT_TRUE(has(out, kRedactedMark)) << line;
        EXPECT_TRUE(holdsCredentials(line)) << line;
    }
    EXPECT_EQ(redactFileText("\turl = " + kRemote + "\n"), "\turl = https://[redacted]@github.com/amd/gaia.git\n");
    EXPECT_EQ(redactFileText("POST https://ci.example.com/hook?token=" + kGlpat + "&x=1\n"),
              "POST https://ci.example.com/hook?token=[redacted]&x=1\n");
}

TEST(RedactFileContentTest, MasksEveryStringAndNeverHidesLines) {
    const json value = {{"content", ".env:3:KEY=1\nurl = " + kRemote + "\n"}, {"lines", json::array({kGhp, 3})}};
    const json out = redactFileContent(value);
    EXPECT_EQ(out["content"].get<std::string>().rfind(".env:3:KEY=1\n", 0), 0u);
    EXPECT_FALSE(has(out.dump(), kGhp));
    EXPECT_EQ(out["lines"][1], 3);
    EXPECT_FALSE(has(out.dump(), kHiddenMark));
}

TEST(SecretsTest, APrivateKeyInToolOutputIsMasked) {
    const std::string printed =
        "$ cat id_rsa\n-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\nq7x9\n"
        "-----END OPENSSH PRIVATE KEY-----\ndone";
    const std::string out = gaia::redactCredentials(printed);
    EXPECT_EQ(out.find("b3BlbnNzaC1rZXk"), std::string::npos) << out;
    EXPECT_NE(out.find("[redacted private key]"), std::string::npos) << out;
    EXPECT_NE(out.find("$ cat id_rsa"), std::string::npos);
    EXPECT_NE(out.find("done"), std::string::npos);
    const std::string cert = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----";
    EXPECT_EQ(gaia::redactCredentials(cert), cert) << "a certificate is public";
}
