// Test process for GAIA process agent suspicious item detection.
// Unsigned binary, runs from build dir, burns CPU + allocates 800 MB RAM.
// Kill with: taskkill /F /IM svc_helper.exe

#include <windows.h>
#include <iostream>
#include <cmath>
#include <thread>
#include <vector>
#include <atomic>

static std::atomic<bool> running{true};

// Continuously read/write memory to keep working set hot
static void churnMemory(char* buf, size_t size) {
    while (running.load()) {
        for (size_t i = 0; i < size; i += 4096)
            buf[i] = static_cast<char>(buf[i] + 1);
        Sleep(100);  // brief pause between passes
    }
}

// CPU burner — compute then yield to cap at ~15-20% per thread
static void burnCpu() {
    volatile double x = 1.0;
    while (running.load()) {
        for (int i = 0; i < 20000000; ++i)
            x = std::sin(x) * std::cos(x) + std::sqrt(std::abs(x) + 1.0);
        Sleep(50);
    }
}

int main() {
    DWORD pid = GetCurrentProcessId();
    std::cout << "svc_helper.exe  PID: " << pid << std::endl;
    std::cout << "  4 memory churners + 2 CPU burners + 800 MB RAM" << std::endl;
    std::cout << "  Kill with: taskkill /F /IM svc_helper.exe" << std::endl;

    // Allocate 400 MB and touch every page so it counts as working set
    constexpr size_t kSize = 800ULL * 1024 * 1024;
    auto* buf = static_cast<char*>(VirtualAlloc(nullptr, kSize, MEM_COMMIT, PAGE_READWRITE));
    if (!buf) {
        std::cerr << "Failed to allocate memory" << std::endl;
        return 1;
    }
    for (size_t i = 0; i < kSize; i += 4096)
        buf[i] = static_cast<char>(i & 0xFF);

    // Split the buffer into 4 regions for parallel churning
    size_t quarter = kSize / 4;
    std::vector<std::thread> threads;
    for (int i = 0; i < 4; ++i)
        threads.emplace_back(churnMemory, buf + i * quarter, quarter);
    for (int i = 0; i < 2; ++i)
        threads.emplace_back(burnCpu);

    // Main thread idles
    while (running.load()) Sleep(1000);

    for (auto& t : threads) if (t.joinable()) t.join();
    VirtualFree(buf, 0, MEM_RELEASE);
    return 0;
}
