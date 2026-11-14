package com.daqin.novelremote;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.Manifest;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Build;
import android.os.Environment;
import android.content.pm.PackageManager;
import android.view.KeyEvent;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.widget.TextView;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebChromeClient;
import android.webkit.DownloadListener;
import android.webkit.CookieManager;
import android.webkit.URLUtil;
import org.json.JSONObject;
import java.util.ArrayList;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;

public class MainActivity extends Activity {

    public static final String HOME_URL = "https://scientificexploration.art/novel/";
    public static final String CHECK_UPDATE_URL = "https://scientificexploration.art/novel/api/version";

    private WebView webView;
    private Handler mainHandler = new Handler(Looper.getMainLooper());
    private int myVersionCode = 16;
    private String myVersionName = "1.11.0";
    private final ArrayList<DownloadManager.Request> pendingDownloads = new ArrayList<>();
    private boolean requestingStoragePermission = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 读取动态包版本信息
        try {
            PackageInfo pInfo = getPackageManager().getPackageInfo(getPackageName(), 0);
            myVersionCode = pInfo.versionCode;
            myVersionName = pInfo.versionName;
        } catch (Exception ignored) {}

        webView = new WebView(this);
        setContentView(webView);
        webView.setWebChromeClient(new WebChromeClient());

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        settings.setSupportZoom(false);
        settings.setBuiltInZoomControls(false);

        // 注入专属 App 标识到 UserAgent，使前端自动识别 App 环境，隐藏下载按钮并应用原生沉浸样式
        String defaultUA = settings.getUserAgentString();
        settings.setUserAgentString(defaultUA + " NovelRemoteHubApp/" + myVersionName);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                Uri uri = Uri.parse(url);
                if ("https".equals(uri.getScheme()) && "scientificexploration.art".equals(uri.getHost())) {
                    return false;
                }
                if ("https".equals(uri.getScheme()) || "http".equals(uri.getScheme())) {
                    startActivity(new Intent(Intent.ACTION_VIEW, uri));
                }
                return true;
            }
        });

        webView.setDownloadListener(new DownloadListener() {
            @Override
            public void onDownloadStart(String url, String userAgent, String disposition, String mimetype, long length) {
                Uri uri = Uri.parse(url);
                if (!"scientificexploration.art".equals(uri.getHost()) || url.contains(".apk")) {
                    startActivity(new Intent(Intent.ACTION_VIEW, uri));
                    return;
                }
                try {
                    DownloadManager.Request request = new DownloadManager.Request(uri);
                    String cookies = CookieManager.getInstance().getCookie(url);
                    if (cookies != null) request.addRequestHeader("Cookie", cookies);
                    request.addRequestHeader("User-Agent", userAgent);
                    String filename = URLUtil.guessFileName(url, disposition, mimetype);
                    request.setTitle(filename);
                    request.setMimeType(mimetype);
                    request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                    request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, filename);
                    pendingDownloads.add(request);
                    if (Build.VERSION.SDK_INT >= 23 && Build.VERSION.SDK_INT <= 28
                            && checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) {
                        if (!requestingStoragePermission) {
                            requestingStoragePermission = true;
                            requestPermissions(new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, 42);
                        }
                    } else {
                        startPendingDownloads();
                    }
                } catch (Exception error) {
                    showDownloadMessage("下载任务建立失败，请重试");
                }
            }
        });
        webView.loadUrl(HOME_URL);

        // 启动时自动检查线上版本
        checkAppUpdate();
    }

    private void startPendingDownloads() {
        DownloadManager manager = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
        int started = 0;
        while (!pendingDownloads.isEmpty()) {
            DownloadManager.Request request = pendingDownloads.remove(0);
            try {
                manager.enqueue(request);
                started++;
            } catch (Exception error) {
                showDownloadMessage("下载任务建立失败，请检查存储空间后重试");
            }
        }
        if (started > 0) showDownloadMessage("已加入系统下载任务，可在下载通知中查看进度");
    }

    private void showDownloadMessage(String message) {
        webView.evaluateJavascript("if(typeof showToast==='function')showToast(" + JSONObject.quote(message) + ");", null);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode != 42) return;
        requestingStoragePermission = false;
        if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) {
            startPendingDownloads();
        } else {
            pendingDownloads.clear();
            showDownloadMessage("未授予存储权限，下载已取消");
        }
    }

    private void checkAppUpdate() {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    URL url = new URL(CHECK_UPDATE_URL);
                    HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                    conn.setConnectTimeout(5000);
                    conn.setReadTimeout(5000);
                    conn.setRequestMethod("GET");

                    if (conn.getResponseCode() == 200) {
                        BufferedReader reader = new BufferedReader(new InputStreamReader(conn.getInputStream(), "UTF-8"));
                        StringBuilder sb = new StringBuilder();
                        String line;
                        while ((line = reader.readLine()) != null) {
                            sb.append(line);
                        }
                        reader.close();

                        String jsonStr = sb.toString();
                        final int remoteVersionCode = extractInt(jsonStr, "versionCode", myVersionCode);
                        final String remoteVersionName = extractString(jsonStr, "versionName", myVersionName);
                        final String downloadUrl = extractString(jsonStr, "apkUrl", HOME_URL + "download/NovelRemoteHub.apk");
                        final String changelog = extractString(jsonStr, "changelog", "有最新版本可用");
                        conn.disconnect();

                        // 只有服务端版本严格大于当前安装的本地版本时才提示更新
                        if (remoteVersionCode > myVersionCode) {
                            mainHandler.post(new Runnable() {
                                @Override
                                public void run() {
                                    showUpdateDialog(remoteVersionName, myVersionName, changelog, downloadUrl);
                                }
                            });
                        }
                    }
                } catch (Exception ignored) {}
            }
        }).start();
    }

    private int extractInt(String json, String key, int defVal) {
        try {
            Matcher m = Pattern.compile("\"" + key + "\"\\s*:\\s*(\\d+)").matcher(json);
            if (m.find()) return Integer.parseInt(m.group(1));
        } catch (Exception e) {}
        return defVal;
    }

    private String extractString(String json, String key, String defVal) {
        try {
            Matcher m = Pattern.compile("\"" + key + "\"\\s*:\\s*\"([^\"]*)\"").matcher(json);
            if (m.find()) return m.group(1).replace("\\n", "\n");
        } catch (Exception e) {}
        return defVal;
    }

    private void showUpdateDialog(String remoteVersion, String localVersion, String changelog, final String downloadUrl) {
        AlertDialog dialog = new AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert)
                .setTitle("🚀 发现新版本 (v" + remoteVersion + ")")
                .setMessage("当前版本: v" + localVersion + "\n最新版本: v" + remoteVersion + "\n\n更新说明：\n" + changelog)
                .setPositiveButton("立即下载更新", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(downloadUrl));
                        startActivity(intent);
                    }
                })
                .setNegativeButton("稍后提醒", null)
                .setCancelable(true)
                .show();
        GradientDrawable background = new GradientDrawable();
        background.setColor(Color.rgb(17, 24, 39));
        background.setCornerRadius(20 * getResources().getDisplayMetrics().density);
        background.setStroke(1, Color.rgb(226, 183, 64));
        if (dialog.getWindow() != null) dialog.getWindow().setBackgroundDrawable(background);
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setTextColor(Color.rgb(226, 183, 64));
        dialog.getButton(AlertDialog.BUTTON_NEGATIVE).setTextColor(Color.rgb(203, 213, 225));
        TextView message = (TextView) dialog.findViewById(android.R.id.message);
        if (message != null) message.setTextColor(Color.rgb(226, 232, 240));
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && webView.canGoBack()) {
            webView.goBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }
}
