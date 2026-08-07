import React from 'react';
import { render, screen } from '@testing-library/react-native';

import { AnalysisFailedState, LowQualityImageState, ProviderUnavailableState, BetaFeatureUnavailableState } from '../components/TrustStates';

// Mock expo-router
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: jest.fn(), push: jest.fn() }),
}));

// Mock safe area context
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 10, bottom: 10 }),
}));

// Mock the camera
jest.mock('expo-camera', () => {
  const { View } = jest.requireActual('react-native');
  return {
    CameraView: (props: any) => <View testID="camera-view" {...props} />,
    useCameraPermissions: () => [{ granted: true }, jest.fn()],
  };
});

describe('Scan Screen Failures', () => {
  it('renders LowQualityImageState without crashing', () => {
    const retake = jest.fn();
    render(<LowQualityImageState guidance={['Needs more light']} onRetake={retake} />);
    expect(screen.getByText(/We could not read that photo clearly/i)).toBeTruthy();
  });

  it('renders ProviderUnavailableState without crashing', () => {
    const retry = jest.fn();
    render(<ProviderUnavailableState onRetry={retry} />);
    expect(screen.getByText(/unavailable right now/i)).toBeTruthy();
  });

  it('renders BetaFeatureUnavailableState without crashing', () => {
    render(<BetaFeatureUnavailableState message="Limit reached" />);
    expect(screen.getByText(/Not available in the private beta/i)).toBeTruthy();
  });

  it('renders AnalysisFailedState without crashing', () => {
    const retry = jest.fn();
    render(<AnalysisFailedState message="Could not process" allowancePreserved={true} onRetry={retry} />);
    expect(screen.getByText(/We could not finish that check/i)).toBeTruthy();
  });
});
