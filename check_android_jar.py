import urllib.request

urls = {
    'android_jar_aliyun': 'https://maven.aliyun.com/repository/public/com/google/android/android/4.1.1.4/android-4.1.1.4.jar',
    'android_robolectric_aliyun': 'https://maven.aliyun.com/repository/public/org/robolectric/android-all/10-robolectric-5803371/android-all-10-robolectric-5803371.jar'
}

for name, u in urls.items():
    try:
        req = urllib.request.Request(u, method='HEAD')
        with urllib.request.urlopen(req, timeout=8) as resp:
            length = int(resp.headers.get("Content-Length", 0)) / (1024 * 1024)
            print(f"{name}: OK ({length:.2f} MB)")
    except Exception as e:
        print(f"{name}: Error {e}")
