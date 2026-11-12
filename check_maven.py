import urllib.request

urls = {
    'aapt2': 'https://repo1.maven.org/maven2/com/android/tools/build/aapt2/8.2.0-10154469/aapt2-8.2.0-10154469-windows.jar',
    'r8': 'https://repo1.maven.org/maven2/com/android/tools/r8/8.2.33/r8-8.2.33.jar',
    'android_jar': 'https://repo1.maven.org/maven2/org/robolectric/android-all/11-robolectric-6757853/android-all-11-robolectric-6757853.jar'
}

for name, u in urls.items():
    try:
        req = urllib.request.Request(u, method='HEAD')
        with urllib.request.urlopen(req, timeout=8) as resp:
            length = int(resp.headers.get("Content-Length", 0)) / (1024 * 1024)
            print(f"{name}: OK ({length:.2f} MB)")
    except Exception as e:
        print(f"{name}: Error {e}")
