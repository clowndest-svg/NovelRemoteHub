import os
import urllib.request
import zipfile

TOOLS_DIR = r"D:\RuanJian\NovelRemoteHub\apk_tools"
os.makedirs(TOOLS_DIR, exist_ok=True)

AAPT2_JAR = os.path.join(TOOLS_DIR, "aapt2.jar")
R8_JAR = os.path.join(TOOLS_DIR, "r8.jar")
ANDROID_JAR = os.path.join(TOOLS_DIR, "android.jar")
AAPT2_EXE = os.path.join(TOOLS_DIR, "aapt2.exe")

urls = {
    AAPT2_JAR: "https://maven.aliyun.com/repository/google/com/android/tools/build/aapt2/8.2.0-10154469/aapt2-8.2.0-10154469-windows.jar",
    R8_JAR: "https://maven.aliyun.com/repository/google/com/android/tools/r8/8.2.33/r8-8.2.33.jar",
    ANDROID_JAR: "https://maven.aliyun.com/repository/public/com/google/android/android/4.1.1.4/android-4.1.1.4.jar"
}

for path, url in urls.items():
    if not os.path.exists(path):
        print(f"Downloading {os.path.basename(path)} from {url}...")
        urllib.request.urlretrieve(url, path)
        print(f"Finished {os.path.basename(path)}")
    else:
        print(f"{os.path.basename(path)} already exists.")

# 提取 aapt2.exe
if not os.path.exists(AAPT2_EXE):
    with zipfile.ZipFile(AAPT2_JAR, 'r') as zf:
        zf.extract("aapt2.exe", TOOLS_DIR)
    print("Extracted aapt2.exe successfully.")
else:
    print("aapt2.exe already extracted.")

print("All APK build tools ready in:", TOOLS_DIR)
