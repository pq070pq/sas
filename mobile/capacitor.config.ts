import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.saspro.app',
  appName: 'SAS PRO',
  webDir: 'www',
  bundledWebRuntime: false,
  ios: { contentInset: 'automatic' },
  android: { allowMixedContent: false },
};

export default config;
