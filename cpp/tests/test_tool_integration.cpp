// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Integration tests for WiFi Agent tool registration/execution and
// Health Agent PowerShell output parsing.  All mock data is anonymized
// (see ANONYMIZATION section below).  No real shell commands are executed.

#include <gtest/gtest.h>
#include <gaia/agent.h>
#include <gaia/clean_console.h>

#include <sstream>

using namespace gaia;

// ===================================================================
// Helper: create a ToolParameter (C++17 aggregate workaround)
// ===================================================================
static ToolParameter makeParam(const std::string& name, ToolParamType type,
                               bool required, const std::string& desc = "") {
    ToolParameter p;
    p.name = name;
    p.type = type;
    p.required = required;
    p.description = desc;
    return p;
}

// ===================================================================
// RAII stdout capture (matches test_clean_console.cpp pattern)
// ===================================================================
class CoutCapture {
public:
    CoutCapture() : captured_(), oldBuf_(std::cout.rdbuf(captured_.rdbuf())) {}
    ~CoutCapture() { std::cout.rdbuf(oldBuf_); }
    std::string str() const { return captured_.str(); }
private:
    std::ostringstream captured_;
    std::streambuf* oldBuf_;
};

// ===================================================================
// ANONYMIZED MOCK DATA
//
// Hostnames      -> TESTPC-001
// MAC addresses  -> AA:BB:CC:DD:EE:01 / AA:BB:CC:DD:EE:02
// Local IPs      -> 10.0.0.100  (host), 10.0.0.1 (gateway)
// User paths     -> C:\Users\testuser\
// SIDs           -> S-1-5-21-000000000-000000000-000000000-1001
// Process IDs    -> 1000, 2000, ...
// GUIDs          -> {00000000-1111-2222-3333-444444444444}
// ===================================================================

// ----- WiFi agent mock outputs -----

static const char* kMockAdapterOutput = R"(
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : RZ717 WiFi 7 160MHz
    GUID                   : {00000000-1111-2222-3333-444444444444}
    Physical address       : AA:BB:CC:DD:EE:01
    Interface type         : Primary
    State                  : disconnected
    Radio status           : Hardware On
                             Software On
)";

static const char* kMockDriverOutput = R"(
Interface name: Wi-Fi

    Driver                    : RZ717 WiFi 7 160MHz
    Vendor                    : MediaTek, Inc.
    Provider                  : MediaTek, Inc.
    Date                      : 3/18/2025
    Version                   : 5.5.0.3548
    INF file                  : oem17.inf
    Type                      : Native Wi-Fi Driver
    Radio types supported     : 802.11b 802.11a 802.11g 802.11n 802.11ac 802.11ax 802.11be
    FIPS 140 mode supported   : Yes
    802.11w Management Frame Protection supported : Yes
    Hosted network supported  : No
)";

static const char* kMockIpConfigOutput = R"(
Windows IP Configuration

   Host Name . . . . . . . . . . . . : TESTPC-001
   Primary Dns Suffix  . . . . . . . :
   Node Type . . . . . . . . . . . . : Hybrid
   IP Routing Enabled. . . . . . . . : No

Ethernet adapter Ethernet 2:

   Description . . . . . . . . . . . : Realtek Gaming 2.5GbE Family Controller
   Physical Address. . . . . . . . . : AA-BB-CC-DD-EE-02
   DHCP Enabled. . . . . . . . . . . : Yes
   IPv4 Address. . . . . . . . . . . : 10.0.0.100(Preferred)
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 10.0.0.1
   DNS Servers . . . . . . . . . . . : 10.0.0.1
)";

static const char* kMockDnsOutput = R"({"Name":"google.com","IPAddress":"142.251.33.206","QueryType":1})";

static const char* kMockInternetOutput = R"({"ComputerName":"8.8.8.8","RemotePort":443,"TcpTestSucceeded":true,"PingSucceeded":false,"PingReplyDetails":null})";

static const char* kMockPingOutput = R"({"ComputerName":"10.0.0.1","RemoteAddress":"10.0.0.1","PingSucceeded":true,"PingReplyDetails":{"Address":"10.0.0.1","RoundtripTime":1,"Status":"Success"}})";

// ----- Health agent mock JSON outputs -----

static const char* kMockMemoryJson      = R"({"TotalGB": 63.65, "FreeGB": 22.56})";
static const char* kMockDiskJson        = R"({"Name": "C", "UsedGB": 1312.44, "FreeGB": 103.34})";
static const char* kMockCpuJson         = R"({"Name": "AMD RYZEN AI MAX+ 395 w/ Radeon 8060S", "LoadPercentage": 6, "NumberOfCores": 16})";
static const char* kMockBatteryJson     = R"({})";
static const char* kMockStorageJson     = R"({"FriendlyName": "PHISON ESR02TBYCCA4-EDJ-2", "MediaType": "SSD", "SizeGB": 1907.7, "HealthStatus": "Healthy", "OperationalStatus": "OK"})";

static const char* kMockGpuJson = R"JSON([
    {"Name":"USB Mobile Monitor Virtual Display","AdapterRAM":null,"DriverVersion":"2.0.0.1","VideoProcessor":null},
    {"Name":"AMD Radeon(TM) 8060S Graphics","AdapterRAM":4293918720,"DriverVersion":"32.0.23027.2005","VideoProcessor":"AMD Radeon Graphics Processor (0x1586)"}
])JSON";

static const char* kMockProcessesJson = R"([
    {"Name":"logioptionsplus_agent","CPU_Sec":36014,"MemMB":129,"Id":1000},
    {"Name":"dllhost","CPU_Sec":33124,"MemMB":11.8,"Id":2000},
    {"Name":"svchost","CPU_Sec":28000,"MemMB":45.2,"Id":3000},
    {"Name":"explorer","CPU_Sec":15000,"MemMB":120.5,"Id":4000},
    {"Name":"chrome","CPU_Sec":12000,"MemMB":350.0,"Id":5000},
    {"Name":"code","CPU_Sec":9500,"MemMB":280.3,"Id":6000},
    {"Name":"WindowsTerminal","CPU_Sec":7000,"MemMB":90.1,"Id":7000},
    {"Name":"RuntimeBroker","CPU_Sec":5500,"MemMB":22.4,"Id":8000},
    {"Name":"SearchHost","CPU_Sec":4000,"MemMB":65.7,"Id":9000},
    {"Name":"SystemSettings","CPU_Sec":3200,"MemMB":18.9,"Id":10000}
])";

static const char* kMockNetworkConfigJson = R"([
    {"InterfaceAlias":"Ethernet 2","IPv4":"10.0.0.100","Gateway":"10.0.0.1","DNS":"10.0.0.1"},
    {"InterfaceAlias":"Wi-Fi","IPv4":"169.254.154.153","Gateway":null,"DNS":""}
])";

static const char* kMockStartupJson = R"([
    {"Name":"AMDNoiseSuppression","Command":"\"C:\\windows\\system32\\AMD\\ANR\\AMDNoiseSuppression.exe\"","Location":"HKU\\S-1-5-21-000000000-000000000-000000000-1001\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run"},
    {"Name":"lemonade-server","Command":"lemonade-server.lnk","Location":"Startup"},
    {"Name":"Discord","Command":"\"C:\\Users\\testuser\\AppData\\Local\\Discord\\Update.exe\" --processStart Discord.exe","Location":"HKU\\S-1-5-21-000000000-000000000-000000000-1001\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run"}
])";

static const char* kMockSystemErrorsJson = R"([
    {"TimeCreated":"/Date(1772491907481)/","Id":10010,"Message":"The server {00000000-1111-2222-3333-444444444444} did not register with DCOM within the required timeout."}
])";

static const char* kMockWindowsUpdatesJson = R"([
    {"HotFixID":"KB5077181","Description":"Security Update","InstalledOn":{"value":"/Date(1770796800000)/","DateTime":"Wednesday, February 11, 2026 12:00:00 AM"}}
])";

static const char* kMockInstalledSoftwareJson = R"([
    {"DisplayName":"AMD Settings","DisplayVersion":"2026.0217.0826.2089","Publisher":"Advanced Micro Devices, Inc.","InstallDate":"20260227"},
    {"DisplayName":"Lemonade Server","DisplayVersion":"10.0.0","Publisher":"AMD","InstallDate":"20260206"}
])";

// ===================================================================
// isSafeShellArg — replicated here because the original is file-static
// in wifi_agent.cpp and cannot be imported.
// ===================================================================
static bool isSafeShellArg(const std::string& arg) {
    for (char c : arg) {
        if (c == ';' || c == '|' || c == '&' || c == '`' || c == '$'
            || c == '(' || c == ')' || c == '{' || c == '}' || c == '<'
            || c == '>' || c == '"' || c == '\n' || c == '\r') {
            return false;
        }
    }
    return !arg.empty();
}

// ===================================================================
// MockWiFiAgent — registers the same tool names/schemas as the real
// WiFiTroubleshooterAgent, but callbacks return hardcoded mock data.
// ===================================================================
class MockWiFiAgent : public Agent {
public:
    explicit MockWiFiAgent(const AgentConfig& config = {}) : Agent(config) {
        init();
    }

protected:
    void registerTools() override {
        // ---- check_adapter ----
        toolRegistry().registerTool(
            "check_adapter",
            "Show Wi-Fi adapter status.",
            [](const json& /*args*/) -> json {
                return {{"tool", "check_adapter"},
                        {"command", "netsh wlan show interfaces"},
                        {"output", kMockAdapterOutput}};
            },
            {}
        );

        // ---- check_wifi_drivers ----
        toolRegistry().registerTool(
            "check_wifi_drivers",
            "Show Wi-Fi driver information.",
            [](const json& /*args*/) -> json {
                return {{"tool", "check_wifi_drivers"},
                        {"command", "netsh wlan show drivers"},
                        {"output", kMockDriverOutput}};
            },
            {}
        );

        // ---- check_ip_config ----
        toolRegistry().registerTool(
            "check_ip_config",
            "Show full IP configuration.",
            [](const json& /*args*/) -> json {
                return {{"tool", "check_ip_config"},
                        {"command", "ipconfig /all"},
                        {"output", kMockIpConfigOutput}};
            },
            {}
        );

        // ---- test_dns_resolution ----
        toolRegistry().registerTool(
            "test_dns_resolution",
            "Test DNS resolution.",
            [](const json& args) -> json {
                std::string hostname = args.value("hostname", "google.com");
                if (!isSafeShellArg(hostname)) {
                    return {{"error", "Invalid hostname -- contains disallowed characters"}};
                }
                std::string cmd = "Resolve-DnsName -Name " + hostname
                    + " -Type A | ConvertTo-Json";
                return {{"tool", "test_dns_resolution"},
                        {"command", cmd},
                        {"hostname", hostname},
                        {"output", kMockDnsOutput}};
            },
            {makeParam("hostname", ToolParamType::STRING, false,
                        "The hostname to resolve (default: google.com)")}
        );

        // ---- test_internet ----
        toolRegistry().registerTool(
            "test_internet",
            "Test internet connectivity.",
            [](const json& /*args*/) -> json {
                return {{"tool", "test_internet"},
                        {"command", "Test-NetConnection -ComputerName 8.8.8.8 -Port 443 | ConvertTo-Json"},
                        {"output", kMockInternetOutput}};
            },
            {}
        );

        // ---- ping_host ----
        toolRegistry().registerTool(
            "ping_host",
            "Ping a specific host.",
            [](const json& args) -> json {
                std::string host = args.value("host", "");
                if (host.empty()) {
                    return {{"error", "host parameter is required"}};
                }
                if (!isSafeShellArg(host)) {
                    return {{"error", "Invalid host -- contains disallowed characters"}};
                }
                std::string cmd = "Test-NetConnection -ComputerName " + host
                    + " | ConvertTo-Json";
                return {{"tool", "ping_host"},
                        {"command", cmd},
                        {"host", host},
                        {"output", kMockPingOutput}};
            },
            {makeParam("host", ToolParamType::STRING, true,
                        "The hostname or IP address to ping")}
        );

        // ---- flush_dns_cache ----
        toolRegistry().registerTool(
            "flush_dns_cache",
            "Clear the local DNS resolver cache.",
            [](const json& /*args*/) -> json {
                return {{"tool", "flush_dns_cache"},
                        {"command", "Clear-DnsClientCache"},
                        {"status", "completed"},
                        {"output", "(no output)"}};
            },
            {}
        );

        // ---- set_dns_servers ----
        toolRegistry().registerTool(
            "set_dns_servers",
            "Set custom DNS server addresses.",
            [](const json& args) -> json {
                std::string adapter = args.value("adapter_name", "");
                std::string primary = args.value("primary_dns", "");
                std::string secondary = args.value("secondary_dns", "");

                if (adapter.empty() || primary.empty()) {
                    return {{"error", "adapter_name and primary_dns are required"}};
                }
                if (!isSafeShellArg(adapter) || !isSafeShellArg(primary) ||
                    (!secondary.empty() && !isSafeShellArg(secondary))) {
                    return {{"error", "Invalid parameter -- contains disallowed characters"}};
                }

                std::string cmd = "Set-DnsClientServerAddress -InterfaceAlias '"
                    + adapter + "' -ServerAddresses ";
                if (secondary.empty()) {
                    cmd += "'" + primary + "'";
                } else {
                    cmd += "('" + primary + "','" + secondary + "')";
                }

                return {
                    {"tool", "set_dns_servers"},
                    {"command", cmd},
                    {"adapter_name", adapter},
                    {"primary_dns", primary},
                    {"secondary_dns", secondary},
                    {"status", "completed"},
                    {"output", "(no output)"}
                };
            },
            {
                makeParam("adapter_name", ToolParamType::STRING, true, "Adapter name"),
                makeParam("primary_dns", ToolParamType::STRING, true, "Primary DNS"),
                makeParam("secondary_dns", ToolParamType::STRING, false, "Secondary DNS")
            }
        );

        // ---- renew_dhcp_lease ----
        toolRegistry().registerTool(
            "renew_dhcp_lease",
            "Release and renew the DHCP lease.",
            [](const json& /*args*/) -> json {
                return {{"tool", "renew_dhcp_lease"},
                        {"command", "ipconfig /release; Start-Sleep -Seconds 1; ipconfig /renew"},
                        {"status", "completed"},
                        {"output", "DHCP lease renewed"}};
            },
            {}
        );

        // ---- restart_wifi_adapter ----
        toolRegistry().registerTool(
            "restart_wifi_adapter",
            "Disable and re-enable a network adapter.",
            [](const json& args) -> json {
                std::string adapter = args.value("adapter_name", "");
                if (adapter.empty()) {
                    return {{"error", "adapter_name is required"}};
                }
                if (!isSafeShellArg(adapter)) {
                    return {{"error", "Invalid adapter_name -- contains disallowed characters"}};
                }
                return {
                    {"tool", "restart_wifi_adapter"},
                    {"command", "Disable-NetAdapter ... Enable-NetAdapter"},
                    {"adapter_name", adapter},
                    {"status", "completed"},
                    {"output", "(no output)"}
                };
            },
            {makeParam("adapter_name", ToolParamType::STRING, true, "Adapter name")}
        );

        // ---- enable_wifi_adapter ----
        toolRegistry().registerTool(
            "enable_wifi_adapter",
            "Enable a disabled Wi-Fi adapter.",
            [](const json& args) -> json {
                std::string adapter = args.value("adapter_name", "");
                if (adapter.empty()) {
                    return {{"error", "adapter_name is required"}};
                }
                if (!isSafeShellArg(adapter)) {
                    return {{"error", "Invalid adapter_name -- contains disallowed characters"}};
                }
                return {
                    {"tool", "enable_wifi_adapter"},
                    {"command", "Enable-NetAdapter -Name '" + adapter + "'"},
                    {"adapter_name", adapter},
                    {"status", "completed"},
                    {"output", "(no output)"}
                };
            },
            {makeParam("adapter_name", ToolParamType::STRING, true, "Adapter name")}
        );

        // ---- toggle_wifi_radio ----
        toolRegistry().registerTool(
            "toggle_wifi_radio",
            "Turn the Wi-Fi radio ON or OFF.",
            [](const json& args) -> json {
                std::string state = args.value("state", "on");
                std::string radioState = (state == "off") ? "Off" : "On";
                return {
                    {"tool", "toggle_wifi_radio"},
                    {"command", "Windows Radio API: Set Wi-Fi radio to " + radioState},
                    {"requested_state", radioState},
                    {"status", "completed"},
                    {"output", "Wi-Fi radio set to " + radioState}
                };
            },
            {makeParam("state", ToolParamType::STRING, false,
                        "The desired radio state: 'on' or 'off' (default: 'on')")}
        );
    }

    std::string getSystemPrompt() const override {
        return "You are a mock WiFi troubleshooter agent for testing.";
    }

public:
    ToolRegistry& tools() { return toolRegistry(); }
};

// ###################################################################
//
//  1. WiFi Agent Tool Integration
//
// ###################################################################

class WiFiToolsTest : public ::testing::Test {
protected:
    void SetUp() override {
        AgentConfig config;
        config.silentMode = true;
        agent_ = std::make_unique<MockWiFiAgent>(config);
    }
    std::unique_ptr<MockWiFiAgent> agent_;
};

TEST_F(WiFiToolsTest, CheckAdapterReturnsExpectedFormat) {
    json result = agent_->tools().executeTool("check_adapter", json::object());
    EXPECT_EQ(result["tool"], "check_adapter");
    EXPECT_EQ(result["command"], "netsh wlan show interfaces");
    std::string output = result["output"].get<std::string>();
    EXPECT_TRUE(output.find("Wi-Fi") != std::string::npos);
    EXPECT_TRUE(output.find("RZ717") != std::string::npos);
    EXPECT_TRUE(output.find("AA:BB:CC:DD:EE:01") != std::string::npos);
    EXPECT_TRUE(output.find("disconnected") != std::string::npos);
    EXPECT_TRUE(output.find("{00000000-1111-2222-3333-444444444444}") != std::string::npos);
}

TEST_F(WiFiToolsTest, CheckWiFiDriversReturnsExpectedFormat) {
    json result = agent_->tools().executeTool("check_wifi_drivers", json::object());
    EXPECT_EQ(result["tool"], "check_wifi_drivers");
    EXPECT_EQ(result["command"], "netsh wlan show drivers");
    std::string output = result["output"].get<std::string>();
    EXPECT_TRUE(output.find("MediaTek") != std::string::npos);
    EXPECT_TRUE(output.find("5.5.0.3548") != std::string::npos);
    EXPECT_TRUE(output.find("802.11be") != std::string::npos);
}

TEST_F(WiFiToolsTest, CheckIpConfigReturnsExpectedFormat) {
    json result = agent_->tools().executeTool("check_ip_config", json::object());
    EXPECT_EQ(result["tool"], "check_ip_config");
    EXPECT_EQ(result["command"], "ipconfig /all");
    std::string output = result["output"].get<std::string>();
    EXPECT_TRUE(output.find("TESTPC-001") != std::string::npos);
    EXPECT_TRUE(output.find("10.0.0.100") != std::string::npos);
    EXPECT_TRUE(output.find("10.0.0.1") != std::string::npos);
    EXPECT_TRUE(output.find("AA-BB-CC-DD-EE-02") != std::string::npos);
}

TEST_F(WiFiToolsTest, TestDnsResolutionDefaultHostname) {
    json result = agent_->tools().executeTool("test_dns_resolution", json::object());
    EXPECT_EQ(result["tool"], "test_dns_resolution");
    EXPECT_EQ(result["hostname"], "google.com");
    // The command should reference google.com as default
    std::string cmd = result["command"].get<std::string>();
    EXPECT_TRUE(cmd.find("google.com") != std::string::npos);
    // Output should be parseable JSON
    json parsedOutput = json::parse(result["output"].get<std::string>());
    EXPECT_EQ(parsedOutput["Name"], "google.com");
    EXPECT_EQ(parsedOutput["IPAddress"], "142.251.33.206");
    EXPECT_EQ(parsedOutput["QueryType"], 1);
}

TEST_F(WiFiToolsTest, TestDnsResolutionCustomHostname) {
    json result = agent_->tools().executeTool(
        "test_dns_resolution", {{"hostname", "cloudflare.com"}});
    EXPECT_EQ(result["hostname"], "cloudflare.com");
    std::string cmd = result["command"].get<std::string>();
    EXPECT_TRUE(cmd.find("cloudflare.com") != std::string::npos);
    // No error should be present
    EXPECT_FALSE(result.contains("error"));
}

TEST_F(WiFiToolsTest, TestInternetReturnsExpectedFormat) {
    json result = agent_->tools().executeTool("test_internet", json::object());
    EXPECT_EQ(result["tool"], "test_internet");
    // Output should be parseable JSON
    json parsedOutput = json::parse(result["output"].get<std::string>());
    EXPECT_EQ(parsedOutput["ComputerName"], "8.8.8.8");
    EXPECT_EQ(parsedOutput["RemotePort"], 443);
    EXPECT_EQ(parsedOutput["TcpTestSucceeded"], true);
    EXPECT_EQ(parsedOutput["PingSucceeded"], false);
    EXPECT_TRUE(parsedOutput["PingReplyDetails"].is_null());
}

TEST_F(WiFiToolsTest, PingHostReturnsExpectedFormat) {
    json result = agent_->tools().executeTool(
        "ping_host", {{"host", "10.0.0.1"}});
    EXPECT_EQ(result["tool"], "ping_host");
    EXPECT_EQ(result["host"], "10.0.0.1");
    std::string cmd = result["command"].get<std::string>();
    EXPECT_TRUE(cmd.find("10.0.0.1") != std::string::npos);
    // Output should be parseable JSON
    json parsedOutput = json::parse(result["output"].get<std::string>());
    EXPECT_EQ(parsedOutput["PingSucceeded"], true);
}

TEST_F(WiFiToolsTest, PingHostMissingArgReturnsError) {
    // Empty host
    json result = agent_->tools().executeTool("ping_host", json::object());
    EXPECT_TRUE(result.contains("error"));
    EXPECT_EQ(result["error"], "host parameter is required");
    EXPECT_FALSE(result.contains("tool"));
}

TEST_F(WiFiToolsTest, FlushDnsCacheReturnsStatus) {
    json result = agent_->tools().executeTool("flush_dns_cache", json::object());
    EXPECT_EQ(result["tool"], "flush_dns_cache");
    EXPECT_EQ(result["status"], "completed");
    EXPECT_EQ(result["command"], "Clear-DnsClientCache");
}

TEST_F(WiFiToolsTest, SetDnsServersMissingArgsReturnsError) {
    // No arguments at all
    json result = agent_->tools().executeTool("set_dns_servers", json::object());
    EXPECT_TRUE(result.contains("error"));
    EXPECT_EQ(result["error"], "adapter_name and primary_dns are required");

    // Only adapter, no primary_dns
    result = agent_->tools().executeTool(
        "set_dns_servers", {{"adapter_name", "Wi-Fi"}});
    EXPECT_TRUE(result.contains("error"));
    EXPECT_EQ(result["error"], "adapter_name and primary_dns are required");
}

TEST_F(WiFiToolsTest, SetDnsServersReturnsExpectedFormat) {
    json result = agent_->tools().executeTool("set_dns_servers", {
        {"adapter_name", "Wi-Fi"},
        {"primary_dns", "8.8.8.8"},
        {"secondary_dns", "8.8.4.4"}
    });
    EXPECT_EQ(result["tool"], "set_dns_servers");
    EXPECT_EQ(result["status"], "completed");
    EXPECT_EQ(result["adapter_name"], "Wi-Fi");
    EXPECT_EQ(result["primary_dns"], "8.8.8.8");
    EXPECT_EQ(result["secondary_dns"], "8.8.4.4");
    // Command should include both DNS servers
    std::string cmd = result["command"].get<std::string>();
    EXPECT_TRUE(cmd.find("8.8.8.8") != std::string::npos);
    EXPECT_TRUE(cmd.find("8.8.4.4") != std::string::npos);
}

TEST_F(WiFiToolsTest, RenewDhcpLeaseReturnsStatus) {
    json result = agent_->tools().executeTool("renew_dhcp_lease", json::object());
    EXPECT_EQ(result["tool"], "renew_dhcp_lease");
    EXPECT_EQ(result["status"], "completed");
    std::string cmd = result["command"].get<std::string>();
    EXPECT_TRUE(cmd.find("ipconfig") != std::string::npos);
}

TEST_F(WiFiToolsTest, RestartWiFiAdapterMissingArgReturnsError) {
    json result = agent_->tools().executeTool("restart_wifi_adapter", json::object());
    EXPECT_TRUE(result.contains("error"));
    EXPECT_EQ(result["error"], "adapter_name is required");
}

TEST_F(WiFiToolsTest, EnableWiFiAdapterMissingArgReturnsError) {
    json result = agent_->tools().executeTool("enable_wifi_adapter", json::object());
    EXPECT_TRUE(result.contains("error"));
    EXPECT_EQ(result["error"], "adapter_name is required");
}

TEST_F(WiFiToolsTest, ToggleWiFiRadioDefaultsToOn) {
    // Default state should be "on"
    json result = agent_->tools().executeTool("toggle_wifi_radio", json::object());
    EXPECT_EQ(result["tool"], "toggle_wifi_radio");
    EXPECT_EQ(result["requested_state"], "On");
    EXPECT_EQ(result["status"], "completed");
    std::string output = result["output"].get<std::string>();
    EXPECT_TRUE(output.find("On") != std::string::npos);

    // Explicit "off"
    result = agent_->tools().executeTool("toggle_wifi_radio", {{"state", "off"}});
    EXPECT_EQ(result["requested_state"], "Off");
}

// ###################################################################
//
//  2. WiFi Agent Input Validation
//
// ###################################################################

TEST(WiFiInputValidation, SafeHostnameAccepted) {
    EXPECT_TRUE(isSafeShellArg("google.com"));
    EXPECT_TRUE(isSafeShellArg("cloudflare.com"));
    EXPECT_TRUE(isSafeShellArg("192.168.1.1"));
    EXPECT_TRUE(isSafeShellArg("my-host.example.org"));
    EXPECT_TRUE(isSafeShellArg("10.0.0.1"));
    EXPECT_TRUE(isSafeShellArg("localhost"));
}

TEST(WiFiInputValidation, UnsafeHostnameRejected) {
    EXPECT_FALSE(isSafeShellArg("host;rm -rf /"));
    EXPECT_FALSE(isSafeShellArg("host|cat /etc/passwd"));
    EXPECT_FALSE(isSafeShellArg("host&whoami"));
    EXPECT_FALSE(isSafeShellArg("host`id`"));
    EXPECT_FALSE(isSafeShellArg("host$PATH"));
    EXPECT_FALSE(isSafeShellArg("host(cmd)"));
    EXPECT_FALSE(isSafeShellArg("host{cmd}"));
    EXPECT_FALSE(isSafeShellArg("host<file"));
    EXPECT_FALSE(isSafeShellArg("host>file"));
    EXPECT_FALSE(isSafeShellArg("host\"quoted"));
    EXPECT_FALSE(isSafeShellArg("host\ninjected"));
    EXPECT_FALSE(isSafeShellArg("host\rinjected"));
}

TEST(WiFiInputValidation, EmptyHostnameRejected) {
    EXPECT_FALSE(isSafeShellArg(""));
}

// ###################################################################
//
//  3. Health Agent PowerShell Output Parsing
//
// ###################################################################

TEST(HealthOutputParsing, MemoryJsonParsesCorrectly) {
    json mem = json::parse(kMockMemoryJson);
    EXPECT_TRUE(mem.contains("TotalGB"));
    EXPECT_TRUE(mem.contains("FreeGB"));
    EXPECT_DOUBLE_EQ(mem["TotalGB"].get<double>(), 63.65);
    EXPECT_DOUBLE_EQ(mem["FreeGB"].get<double>(), 22.56);
}

TEST(HealthOutputParsing, DiskJsonParsesCorrectly) {
    json disk = json::parse(kMockDiskJson);
    EXPECT_EQ(disk["Name"], "C");
    EXPECT_DOUBLE_EQ(disk["UsedGB"].get<double>(), 1312.44);
    EXPECT_DOUBLE_EQ(disk["FreeGB"].get<double>(), 103.34);
}

TEST(HealthOutputParsing, CpuJsonParsesCorrectly) {
    json cpu = json::parse(kMockCpuJson);
    EXPECT_EQ(cpu["Name"], "AMD RYZEN AI MAX+ 395 w/ Radeon 8060S");
    EXPECT_EQ(cpu["LoadPercentage"], 6);
    EXPECT_EQ(cpu["NumberOfCores"], 16);
}

TEST(HealthOutputParsing, GpuJsonParsesCorrectly) {
    json gpu = json::parse(kMockGpuJson);
    ASSERT_TRUE(gpu.is_array());
    ASSERT_EQ(gpu.size(), 2u);

    // First GPU — virtual display with null fields
    EXPECT_EQ(gpu[0]["Name"], "USB Mobile Monitor Virtual Display");
    EXPECT_TRUE(gpu[0]["AdapterRAM"].is_null());
    EXPECT_EQ(gpu[0]["DriverVersion"], "2.0.0.1");
    EXPECT_TRUE(gpu[0]["VideoProcessor"].is_null());

    // Second GPU — AMD Radeon with real values
    EXPECT_EQ(gpu[1]["Name"], "AMD Radeon(TM) 8060S Graphics");
    EXPECT_EQ(gpu[1]["AdapterRAM"].get<int64_t>(), 4293918720);
    EXPECT_EQ(gpu[1]["DriverVersion"], "32.0.23027.2005");
    EXPECT_EQ(gpu[1]["VideoProcessor"], "AMD Radeon Graphics Processor (0x1586)");
}

TEST(HealthOutputParsing, ProcessesJsonParsesCorrectly) {
    json procs = json::parse(kMockProcessesJson);
    ASSERT_TRUE(procs.is_array());
    ASSERT_EQ(procs.size(), 10u);

    // Verify first process
    EXPECT_EQ(procs[0]["Name"], "logioptionsplus_agent");
    EXPECT_EQ(procs[0]["CPU_Sec"], 36014);
    EXPECT_EQ(procs[0]["MemMB"], 129);
    EXPECT_EQ(procs[0]["Id"], 1000);

    // Verify last process
    EXPECT_EQ(procs[9]["Name"], "SystemSettings");
    EXPECT_EQ(procs[9]["Id"], 10000);

    // Verify all have required fields
    for (const auto& proc : procs) {
        EXPECT_TRUE(proc.contains("Name"));
        EXPECT_TRUE(proc.contains("CPU_Sec"));
        EXPECT_TRUE(proc.contains("MemMB"));
        EXPECT_TRUE(proc.contains("Id"));
    }
}

TEST(HealthOutputParsing, NetworkConfigJsonParsesCorrectly) {
    json net = json::parse(kMockNetworkConfigJson);
    ASSERT_TRUE(net.is_array());
    ASSERT_EQ(net.size(), 2u);

    // First interface — Ethernet with all fields populated
    EXPECT_EQ(net[0]["InterfaceAlias"], "Ethernet 2");
    EXPECT_EQ(net[0]["IPv4"], "10.0.0.100");
    EXPECT_EQ(net[0]["Gateway"], "10.0.0.1");
    EXPECT_EQ(net[0]["DNS"], "10.0.0.1");

    // Second interface — Wi-Fi with null gateway and empty DNS
    EXPECT_EQ(net[1]["InterfaceAlias"], "Wi-Fi");
    EXPECT_EQ(net[1]["IPv4"], "169.254.154.153");
    EXPECT_TRUE(net[1]["Gateway"].is_null());
    EXPECT_EQ(net[1]["DNS"], "");
}

TEST(HealthOutputParsing, StartupProgramsJsonParsesCorrectly) {
    json startup = json::parse(kMockStartupJson);
    ASSERT_TRUE(startup.is_array());
    ASSERT_EQ(startup.size(), 3u);

    EXPECT_EQ(startup[0]["Name"], "AMDNoiseSuppression");
    EXPECT_TRUE(startup[0]["Command"].get<std::string>().find("AMDNoiseSuppression.exe") != std::string::npos);
    EXPECT_TRUE(startup[0]["Location"].get<std::string>().find("S-1-5-21-000000000") != std::string::npos);

    EXPECT_EQ(startup[1]["Name"], "lemonade-server");
    EXPECT_EQ(startup[1]["Location"], "Startup");

    EXPECT_EQ(startup[2]["Name"], "Discord");
    EXPECT_TRUE(startup[2]["Command"].get<std::string>().find("testuser") != std::string::npos);
}

TEST(HealthOutputParsing, SystemErrorsJsonParsesCorrectly) {
    json errors = json::parse(kMockSystemErrorsJson);
    ASSERT_TRUE(errors.is_array());
    ASSERT_EQ(errors.size(), 1u);

    // Verify Date format string is preserved
    std::string timeCreated = errors[0]["TimeCreated"].get<std::string>();
    EXPECT_TRUE(timeCreated.find("/Date(") != std::string::npos);
    EXPECT_TRUE(timeCreated.find(")/") != std::string::npos);

    EXPECT_EQ(errors[0]["Id"], 10010);
    EXPECT_TRUE(errors[0]["Message"].get<std::string>().find("DCOM") != std::string::npos);
    // Verify anonymized GUID in message
    EXPECT_TRUE(errors[0]["Message"].get<std::string>().find("00000000-1111-2222-3333-444444444444") != std::string::npos);
}

TEST(HealthOutputParsing, WindowsUpdatesJsonParsesCorrectly) {
    json updates = json::parse(kMockWindowsUpdatesJson);
    ASSERT_TRUE(updates.is_array());
    ASSERT_EQ(updates.size(), 1u);

    EXPECT_EQ(updates[0]["HotFixID"], "KB5077181");
    EXPECT_EQ(updates[0]["Description"], "Security Update");

    // Verify nested InstalledOn object
    ASSERT_TRUE(updates[0]["InstalledOn"].is_object());
    json installedOn = updates[0]["InstalledOn"];
    EXPECT_TRUE(installedOn.contains("value"));
    EXPECT_TRUE(installedOn.contains("DateTime"));
    std::string dateValue = installedOn["value"].get<std::string>();
    EXPECT_TRUE(dateValue.find("/Date(") != std::string::npos);
    std::string dateTime = installedOn["DateTime"].get<std::string>();
    EXPECT_TRUE(dateTime.find("February") != std::string::npos);
}

TEST(HealthOutputParsing, BatteryEmptyJsonHandledCorrectly) {
    json battery = json::parse(kMockBatteryJson);
    EXPECT_TRUE(battery.is_object());
    EXPECT_TRUE(battery.empty());
    // Verify graceful handling: no crash, no fields
    EXPECT_FALSE(battery.contains("Status"));
    EXPECT_FALSE(battery.contains("ChargePercent"));
}

TEST(HealthOutputParsing, InstalledSoftwareJsonParsesCorrectly) {
    json software = json::parse(kMockInstalledSoftwareJson);
    ASSERT_TRUE(software.is_array());
    ASSERT_EQ(software.size(), 2u);

    EXPECT_EQ(software[0]["DisplayName"], "AMD Settings");
    EXPECT_EQ(software[0]["DisplayVersion"], "2026.0217.0826.2089");
    EXPECT_EQ(software[0]["Publisher"], "Advanced Micro Devices, Inc.");
    EXPECT_EQ(software[0]["InstallDate"], "20260227");

    EXPECT_EQ(software[1]["DisplayName"], "Lemonade Server");
    EXPECT_EQ(software[1]["Publisher"], "AMD");
}

TEST(HealthOutputParsing, StorageHealthJsonParsesCorrectly) {
    json storage = json::parse(kMockStorageJson);
    EXPECT_EQ(storage["FriendlyName"], "PHISON ESR02TBYCCA4-EDJ-2");
    EXPECT_EQ(storage["MediaType"], "SSD");
    EXPECT_DOUBLE_EQ(storage["SizeGB"].get<double>(), 1907.7);
    EXPECT_EQ(storage["HealthStatus"], "Healthy");
    EXPECT_EQ(storage["OperationalStatus"], "OK");
}

// ###################################################################
//
//  4. Tool Result -> CleanConsole Pipeline
//
// ###################################################################

TEST(ToolConsoleIntegration, WiFiToolResultRendersInConsole) {
    CleanConsole console;
    CoutCapture cap;

    json result = {
        {"tool", "check_adapter"},
        {"command", "netsh wlan show interfaces"},
        {"output", "Name: Wi-Fi\nState: connected"}
    };
    console.prettyPrintJson(result, "Tool Result");

    std::string out = cap.str();
    // Should show the command
    EXPECT_TRUE(out.find("Cmd:") != std::string::npos)
        << "Expected Cmd: label; got: " << out;
    EXPECT_TRUE(out.find("netsh wlan show interfaces") != std::string::npos)
        << "Expected command text; got: " << out;
    // Should show the output
    EXPECT_TRUE(out.find("Output:") != std::string::npos)
        << "Expected Output: label; got: " << out;
    EXPECT_TRUE(out.find("Wi-Fi") != std::string::npos)
        << "Expected output content; got: " << out;
}

TEST(ToolConsoleIntegration, WiFiToolArgsRendersInConsole) {
    CleanConsole console;
    CoutCapture cap;

    json args = {{"hostname", "google.com"}};
    console.prettyPrintJson(args, "Tool Args");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Args:") != std::string::npos)
        << "Expected Args: label; got: " << out;
    EXPECT_TRUE(out.find("hostname") != std::string::npos)
        << "Expected key 'hostname'; got: " << out;
    EXPECT_TRUE(out.find("google.com") != std::string::npos)
        << "Expected value 'google.com'; got: " << out;
}

TEST(ToolConsoleIntegration, ToolResultWithErrorRendersRedLabel) {
    CleanConsole console;
    CoutCapture cap;

    json result = {{"error", "host parameter is required"}};
    console.prettyPrintJson(result, "Tool Result");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Error:") != std::string::npos)
        << "Expected Error: label; got: " << out;
    EXPECT_TRUE(out.find("host parameter is required") != std::string::npos)
        << "Expected error message; got: " << out;
    // The red ANSI code should appear
    EXPECT_TRUE(out.find("\033[91m") != std::string::npos)
        << "Expected red ANSI code for error; got: " << out;
}

TEST(ToolConsoleIntegration, ToolResultWithCommandShowsCmd) {
    CleanConsole console;
    CoutCapture cap;

    json result = {
        {"command", "Clear-DnsClientCache"},
        {"status", "completed"},
        {"output", "(no output)"}
    };
    console.prettyPrintJson(result, "Tool Result");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Cmd:") != std::string::npos)
        << "Expected Cmd: label; got: " << out;
    EXPECT_TRUE(out.find("Clear-DnsClientCache") != std::string::npos)
        << "Expected command text; got: " << out;
}

TEST(ToolConsoleIntegration, ToolResultWithLongOutputTruncates) {
    CleanConsole console;
    CoutCapture cap;

    // Build output with 15 lines (exceeds kMaxPreviewLines = 10)
    std::string longOutput;
    for (int i = 1; i <= 15; ++i) {
        longOutput += "Adapter line " + std::to_string(i) + "\n";
    }

    json result = {{"output", longOutput}};
    console.prettyPrintJson(result, "Tool Result");

    std::string out = cap.str();
    // First 10 lines should appear
    EXPECT_TRUE(out.find("Adapter line 1") != std::string::npos)
        << "Expected first line; got: " << out;
    EXPECT_TRUE(out.find("Adapter line 10") != std::string::npos)
        << "Expected 10th line; got: " << out;
    // Lines beyond 10 should NOT appear
    EXPECT_TRUE(out.find("Adapter line 11") == std::string::npos)
        << "Line 11 should NOT appear; got: " << out;
    // Truncation message
    EXPECT_TRUE(out.find("5 more lines") != std::string::npos)
        << "Expected '5 more lines' truncation message; got: " << out;
}

TEST(ToolConsoleIntegration, HealthMcpResultRendersInConsole) {
    CleanConsole console;
    CoutCapture cap;

    // Simulate an MCP tool result for health agent -- the output field
    // contains the raw JSON string from PowerShell.
    json result = {
        {"output", kMockMemoryJson}
    };
    console.prettyPrintJson(result, "Tool Result");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Output:") != std::string::npos)
        << "Expected Output: label; got: " << out;
    // The raw JSON content should be visible
    EXPECT_TRUE(out.find("TotalGB") != std::string::npos)
        << "Expected TotalGB in output; got: " << out;
    EXPECT_TRUE(out.find("63.65") != std::string::npos)
        << "Expected memory value in output; got: " << out;
}

// ###################################################################
//
//  5. Mock WiFi Agent Full Chain
//
// ###################################################################

class WiFiFullChain : public ::testing::Test {
protected:
    void SetUp() override {
        AgentConfig config;
        config.silentMode = true;
        agent_ = std::make_unique<MockWiFiAgent>(config);
    }
    std::unique_ptr<MockWiFiAgent> agent_;
};

TEST_F(WiFiFullChain, DiagnosticToolChainExecutes) {
    // Execute the diagnostic tools in sequence and verify each returns valid data
    json r1 = agent_->tools().executeTool("check_adapter", json::object());
    EXPECT_EQ(r1["tool"], "check_adapter");
    EXPECT_FALSE(r1.contains("error"));

    json r2 = agent_->tools().executeTool("check_ip_config", json::object());
    EXPECT_EQ(r2["tool"], "check_ip_config");
    EXPECT_FALSE(r2.contains("error"));

    json r3 = agent_->tools().executeTool("test_dns_resolution", json::object());
    EXPECT_EQ(r3["tool"], "test_dns_resolution");
    EXPECT_FALSE(r3.contains("error"));

    json r4 = agent_->tools().executeTool("test_internet", json::object());
    EXPECT_EQ(r4["tool"], "test_internet");
    EXPECT_FALSE(r4.contains("error"));

    // Verify the outputs can all be inspected as strings
    EXPECT_TRUE(r1["output"].is_string());
    EXPECT_TRUE(r2["output"].is_string());
    EXPECT_TRUE(r3["output"].is_string());
    EXPECT_TRUE(r4["output"].is_string());

    // Verify that the DNS and Internet outputs are parseable JSON
    EXPECT_NO_THROW({ auto parsed = json::parse(r3["output"].get<std::string>()); (void)parsed; });
    EXPECT_NO_THROW({ auto parsed = json::parse(r4["output"].get<std::string>()); (void)parsed; });
}

TEST_F(WiFiFullChain, FixToolChainExecutes) {
    // Execute flush_dns_cache and verify the status is completed
    json r1 = agent_->tools().executeTool("flush_dns_cache", json::object());
    EXPECT_EQ(r1["tool"], "flush_dns_cache");
    EXPECT_EQ(r1["status"], "completed");
    EXPECT_FALSE(r1.contains("error"));

    // After flushing DNS, verify dns resolution tool still works
    json r2 = agent_->tools().executeTool("test_dns_resolution", json::object());
    EXPECT_EQ(r2["tool"], "test_dns_resolution");
    EXPECT_FALSE(r2.contains("error"));
}
