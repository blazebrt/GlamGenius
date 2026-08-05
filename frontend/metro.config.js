// metro.config.js
const { getDefaultConfig } = require("expo/metro-config");
const path = require('path');
const { FileStore } = require('metro-cache');

const config = getDefaultConfig(__dirname);

// Use a stable on-disk store (shared across web/android)
const root = process.env.METRO_CACHE_ROOT || path.join(__dirname, '.metro-cache');
config.cacheStores = [
  new FileStore({ root: path.join(root, 'cache') }),
];


// // Exclude unnecessary directories from file watching
// config.watchFolders = [__dirname];
// config.resolver.blacklistRE = /(.*)\/(__tests__|android|ios|build|dist|.git|node_modules\/.*\/android|node_modules\/.*\/ios|node_modules\/.*\/windows|node_modules\/.*\/macos)(\/.*)?$/;

// // Alternative: use a more aggressive exclusion pattern
// config.resolver.blacklistRE = /node_modules\/.*\/(android|ios|windows|macos|__tests__|\.git|.*\.android\.js|.*\.ios\.js)$/;

// Reduce the number of workers to decrease resource usage
config.maxWorkers = 2;

// --- Supabase on React Native ------------------------------------------------
//
// `@supabase/supabase-js` pulls in `@supabase/realtime-js`, which requires the
// Node `ws` package, which requires Node's `stream` core module. React Native
// has no Node standard library, so the Android bundle failed with:
//
//     Unable to resolve module stream from
//     node_modules/@supabase/realtime-js/node_modules/ws/lib/stream.js
//
// The `ws` branch is unreachable on a device: realtime-js only reaches for it
// when there is no global WebSocket, and React Native always has one. Metro
// still follows the require statically, so it has to resolve to something.
//
// `@supabase/realtime-js@2.11.2` declares no `browser`, `react-native` or
// `exports` entry, so the usual "resolve under the browser condition" fix
// (https://github.com/expo/expo/discussions/36551) has nothing to select — it
// is a no-op here. Pointing `ws` at a shim that returns the runtime's own
// WebSocket removes the Node dependency chain instead. See `shims/ws.js`.
//
// Native platforms only. On web, `ws` resolves normally.
const wsShim = path.resolve(__dirname, 'shims/ws.js');
const baseResolveRequest = config.resolver.resolveRequest;

config.resolver.resolveRequest = (context, moduleImport, platform) => {
  const resolve = baseResolveRequest ?? context.resolveRequest;
  const isNative = platform === 'android' || platform === 'ios';
  if (isNative && (moduleImport === 'ws' || moduleImport.startsWith('ws/'))) {
    return { type: 'sourceFile', filePath: wsShim };
  }
  return resolve(context, moduleImport, platform);
};

module.exports = config;
