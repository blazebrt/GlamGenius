/**
 * React Native stand-in for the Node `ws` package.
 *
 * `@supabase/realtime-js` requires `ws`, and `ws` requires Node's `stream`
 * core module, which React Native does not have. That broke the Android
 * bundle even though the code path is never taken on a device:
 *
 *     // realtime-js/dist/main/RealtimeClient.js
 *     const NATIVE_WEBSOCKET_AVAILABLE = typeof WebSocket !== 'undefined';
 *     ...
 *     if (NATIVE_WEBSOCKET_AVAILABLE) {
 *       this.conn = new WebSocket(this.endpointURL());   // React Native
 *       return;
 *     }
 *     ...require('ws')...                                // Node only
 *
 * React Native always provides a global `WebSocket`, so the `ws` branch is
 * unreachable there. Metro still follows the `require` statically, so the
 * import has to resolve to something. This module is that something: it hands
 * back the runtime's own WebSocket, so even if the branch were somehow taken
 * the result would work rather than explode.
 *
 * GlamGenius does not use Supabase Realtime at all — the app uses Supabase for
 * Auth only, and every product call goes through the FastAPI backend. Nothing
 * in the app opens a channel or a connection.
 *
 * Remove this shim when `@supabase/supabase-js` is upgraded past the release
 * that fixes the dependency upstream (2.49.5+), along with the `ws` branch of
 * the resolver in `metro.config.js`.
 */
const RuntimeWebSocket = typeof WebSocket !== 'undefined' ? WebSocket : undefined;

module.exports = RuntimeWebSocket;
module.exports.default = RuntimeWebSocket;
module.exports.WebSocket = RuntimeWebSocket;
