import urllib.request

urls = {
    'aapt2_google': 'https://dl.google.com/dl/android/maven2/com/android/tools/build/aapt2/8.2.0-10154469/aapt2-8.2.0-10154469-windows.jar',
    'aapt2_aliyun': 'https://maven.aliyun.com/repository/google/com/android/tools/build/aapt2/8.2.0-10154469/aapt2-8.2.0-10154469-windows.jar',
    'r8_google': 'https://dl.google.com/dl/android/maven2/com/android/tools/r8/8.2.33/r8-8.2.33.jar',
    'r8_aliyun': 'https://maven.aliyun.com/repository/google/com/android/tools/r8/8.2.33/r8-8.2.33.jar'
}

for name, u in urls.items():
    try:
        req = urllib.request.Request(u, method='HEAD')
        with urllib.request.urlopen(req, timeout=8) as resp:
            length = int(resp.headers.get("Content-Length", 0)) / (1024 * 1024)
            print(f"{name}: OK ({length:.2f} MB)")
    except Exception as e:
        print(f"{name}: Error {e}")
