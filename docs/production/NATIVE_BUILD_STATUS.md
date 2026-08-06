# Native Build Status and Critical Journey

This document outlines the native build processes for GlamGenius (Android & iOS), the critical journey validated on native binaries, and the current state of production readiness regarding native artifacts.

## Current Build Status

**Android (APK/AAB):** INCOMPLETE (Missing EAS Credentials / Keystore)
**iOS (IPA):** INCOMPLETE (Missing Apple Developer Account / Provisioning Profiles / Hardware)

*Note: A Metro bundler export is not a native build. The app must be compiled into native binaries via EAS (Expo Application Services) or local Android Studio / Xcode before it can be submitted to the stores.*

## EAS Build Process

1. **Prerequisites**: Expo CLI installed (`npm install -g eas-cli`), logged into EAS (`eas login`).
2. **Configuration**: `eas.json` is configured for development, preview, and production profiles.
3. **Android Build**: `eas build --platform android --profile production`
4. **iOS Build**: `eas build --platform ios --profile production`
5. **Artifacts**: Download the `.aab` (Android App Bundle) and `.ipa` (iOS App Store Package) from the Expo dashboard.

## Native Critical Journey (38 Steps)

To ensure the native builds are production-ready, the following 38 steps must be manually validated on real physical devices (not just simulators/emulators):

### Authentication & Onboarding
1. App launches from cold start without crashing.
2. Splash screen displays correctly and transitions smoothly.
3. Sign up with a new email address.
4. Sign up with Google/Apple SSO (Native integrations).
5. Verify email address via deep link.
6. Login with existing credentials.
7. Reset password flow via deep link.
8. Complete the initial profile setup.

### Profile & Permissions
9. Request Camera permissions (Native prompt).
10. Request Photo Library permissions (Native prompt).
11. Handle permission denial gracefully.
12. Update profile details.
13. Upload profile picture from gallery.
14. Take a new profile picture using the camera.

### Core Features (AI Gateway)
15. Upload a photo for Scan Analysis.
16. Verify the AI parsing speed and loading states.
17. Review occasion styling recommendations.
18. Test shopping evaluation by uploading a product photo.
19. Review routine assistance recommendations.
20. Check offline caching for previously loaded analyses.

### Wardrobe & Inventory
21. Add an item to the wardrobe via camera.
22. Edit an existing wardrobe item.
23. Delete a wardrobe item.
24. Scroll through a large wardrobe list (verify FlatList performance).

### Push Notifications
25. Request Push Notification permissions.
26. Receive a test push notification in the foreground.
27. Receive a test push notification in the background.
28. Tap a notification to open a specific screen (Deep linking).

### Network & Error Handling
29. Simulate offline mode (Airplane mode) and verify error messages.
30. Reconnect to the network and verify auto-retry.
31. Trigger a simulated Sentry error and verify it is captured.

### Settings & Account Management
32. Change theme (Light/Dark mode) and verify UI updates immediately.
33. Access Privacy Policy (opens in-app browser or native browser).
34. Access Terms of Service.
35. Log out successfully.
36. Initiate account deletion (End-to-end cascade verification).

### Performance & Edge Cases
37. Background the app for 5 minutes and resume (verify state is kept).
38. Navigate rapidly between tabs (verify no memory leaks or crashes).

## Conclusion

Until the requisite Apple/Google developer accounts and hardware are provided, the native build process cannot be fully executed and the 38-step journey cannot be validated on physical devices. **Production sign-off for native apps is blocked.**
