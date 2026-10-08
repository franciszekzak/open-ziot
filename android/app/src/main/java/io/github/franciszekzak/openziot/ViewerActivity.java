package io.github.franciszekzak.openziot;

import android.app.*;
import android.content.*;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.webkit.*;
import android.widget.*;
import java.net.URI;

/** HTTPS viewer hosted by the owner; credentials stay in memory for this view. */
public class ViewerActivity extends Activity {
  private WebView web;

  public static void validate(String url) {
    try {
      URI parsed = new URI(url);
      if (!"https".equalsIgnoreCase(parsed.getScheme())
          || parsed.getHost() == null
          || parsed.getRawUserInfo() != null
          || parsed.getRawFragment() != null) throw new IllegalArgumentException();
    } catch (Exception error) {
      throw new IllegalArgumentException("Viewer URL must be HTTPS, without embedded credentials");
    }
  }

  @Override
  public void onCreate(Bundle saved) {
    super.onCreate(saved);
    String url = getIntent().getStringExtra("url");
    validate(url);
    URI origin = URI.create(url);
    web = new WebView(this);
    web.getSettings().setJavaScriptEnabled(true);
    web.getSettings().setDomStorageEnabled(true);
    web.getSettings().setAllowFileAccess(false);
    web.getSettings().setAllowContentAccess(false);
    web.getSettings().setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
    web.setWebViewClient(
        new WebViewClient() {
          @Override
          public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            URI next = URI.create(request.getUrl().toString());
            return !"https".equals(next.getScheme())
                || !origin.getHost().equalsIgnoreCase(next.getHost())
                || origin.getPort() != next.getPort();
          }

          @Override
          public void onReceivedHttpAuthRequest(
              WebView view, HttpAuthHandler handler, String host, String realm) {
            if (!origin.getHost().equalsIgnoreCase(host)) {
              handler.cancel();
              return;
            }
            LinearLayout form = new LinearLayout(ViewerActivity.this);
            form.setOrientation(LinearLayout.VERTICAL);
            form.setPadding(32, 16, 32, 16);
            EditText user = new EditText(ViewerActivity.this);
            user.setHint("Viewer username");
            user.setSingleLine();
            form.addView(user);
            EditText password = new EditText(ViewerActivity.this);
            password.setHint("Viewer password");
            password.setSingleLine();
            password.setSaveEnabled(false);
            password.setInputType(
                InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
            form.addView(password);
            new AlertDialog.Builder(ViewerActivity.this)
                .setTitle("Sign in to " + host)
                .setView(form)
                .setPositiveButton(
                    "Sign in",
                    (dialog, which) ->
                        handler.proceed(user.getText().toString(), password.getText().toString()))
                .setNegativeButton("Cancel", (dialog, which) -> handler.cancel())
                .setOnCancelListener(dialog -> handler.cancel())
                .show();
          }
        });
    web.setDownloadListener(
        (download, agent, disposition, type, length) -> {
          validate(download);
          startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(download)));
        });
    web.setWebChromeClient(new WebChromeClient());
    web.setOnApplyWindowInsetsListener(
        (view, insets) -> {
          view.setPadding(
              0, insets.getSystemWindowInsetTop(), 0, insets.getSystemWindowInsetBottom());
          return insets;
        });
    setContentView(web);
    web.loadUrl(url);
  }

  @Override
  public void onBackPressed() {
    if (web.canGoBack()) web.goBack();
    else super.onBackPressed();
  }

  @Override
  protected void onDestroy() {
    web.stopLoading();
    web.destroy();
    super.onDestroy();
  }
}
