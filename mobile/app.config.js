const policy = require("../nyx/settings/frontend.json").mobileAndroid;

module.exports = ({ config }) => {
  const origin = new URL(process.env.NYX_MOBILE_ORIGIN ?? policy.origin);
  if (origin.protocol !== "https:" || origin.href !== origin.origin + "/") {
    throw new Error("NYX_MOBILE_ORIGIN must be an HTTPS origin");
  }
  return {
    ...config,
    name: policy.app_name,
    slug: "nyx-mobile",
    version: "0.1.2",
    platforms: ["android"],
    android: {
      package: "app.nyx.mobile",
      versionCode: 3,
      icon: "./assets/icon.png",
      adaptiveIcon: { foregroundImage: "./assets/icon-adaptive.png" },
      softwareKeyboardLayoutMode: "resize",
    },
    plugins: ["expo-secure-store"],
    extra: {
      ...config.extra,
      nyxOrigin: origin.origin,
    },
  };
};
