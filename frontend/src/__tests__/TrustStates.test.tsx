/**
 * The trust states are what a user sees when something goes wrong, so what they
 * say — and what they do not say — is the behaviour under test.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react-native';
import {
  AnalysisFailedState,
  BetaFeatureUnavailableState,
  DeletionConfirmState,
  LowQualityImageState,
  PhotoAnalysisConsentControl,
  ProcessingState,
  ProviderUnavailableState,
  RetryingState,
  UploadingState,
} from '../components/TrustStates';

describe('photo analysis consent', () => {
  it('states that the photo is analysed but not stored', () => {
    render(<PhotoAnalysisConsentControl checked={false} onChange={jest.fn()} />);
    expect(screen.getByText(/not stored/i)).toBeTruthy();
    expect(screen.getByTestId('photo-consent-toggle').props.accessibilityState.checked).toBe(false);
  });

  it('requires an explicit toggle', () => {
    const onChange = jest.fn();
    render(<PhotoAnalysisConsentControl checked={false} onChange={onChange} />);
    fireEvent.press(screen.getByTestId('photo-consent-toggle'));
    expect(onChange).toHaveBeenCalledWith(true);
  });
});

describe('progress states', () => {
  it('renders uploading, with progress when known', () => {
    render(<UploadingState progress={0.42} />);
    expect(screen.getByTestId('state-uploading')).toBeTruthy();
    expect(screen.getByText(/42%/)).toBeTruthy();
  });

  it('renders processing', () => {
    render(<ProcessingState />);
    expect(screen.getByTestId('state-processing')).toBeTruthy();
  });

  it('says a retry has not cost the user anything', () => {
    render(<RetryingState attempt={2} maxAttempts={3} />);
    expect(screen.getByTestId('state-retrying')).toBeTruthy();
    expect(screen.getByText(/2 of 3/)).toBeTruthy();
    expect(screen.getByText(/has not been used/i)).toBeTruthy();
  });
});

describe('LowQualityImageState', () => {
  it('shows every piece of guidance the server sent', () => {
    render(
      <LowQualityImageState
        guidance={['Face a window', 'Hold the camera at eye level']}
        onRetake={jest.fn()}
      />
    );

    expect(screen.getByText('Face a window')).toBeTruthy();
    expect(screen.getByText('Hold the camera at eye level')).toBeTruthy();
  });

  it('offers a retake and reports it', () => {
    const onRetake = jest.fn();
    render(<LowQualityImageState guidance={[]} onRetake={onRetake} />);

    fireEvent.press(screen.getByTestId('retake'));
    expect(onRetake).toHaveBeenCalled();
  });
});

describe('ProviderUnavailableState', () => {
  it('blames the right party', () => {
    render(<ProviderUnavailableState onRetry={jest.fn()} />);

    expect(screen.getByText(/problem on our side/i)).toBeTruthy();
    expect(screen.getByText(/not with your photo/i)).toBeTruthy();
  });

  it('hides retry when the server says retrying will not help', () => {
    render(<ProviderUnavailableState onRetry={jest.fn()} retryable={false} />);
    expect(screen.queryByTestId('retry')).toBeNull();
  });
});

describe('AnalysisFailedState', () => {
  it('reassures about the allowance only when it is confirmed', () => {
    const { rerender } = render(
      <AnalysisFailedState
        message="We could not finish."
        allowancePreserved
        onRetry={jest.fn()}
      />
    );
    expect(screen.getByTestId('allowance-preserved')).toBeTruthy();

    rerender(
      <AnalysisFailedState
        message="We could not finish."
        allowancePreserved={false}
        onRetry={jest.fn()}
      />
    );
    // Claiming a refund we cannot confirm would be worse than saying nothing.
    expect(screen.queryByTestId('allowance-preserved')).toBeNull();
  });

  it('never renders anything resembling a personalised result', () => {
    render(
      <AnalysisFailedState
        message="We could not analyse this image reliably."
        guidance={['Try better light']}
        allowancePreserved
        onRetry={jest.fn()}
      />
    );

    // The old fallback's invented content. None of it may appear on a failure.
    for (const invented of [/wheatish/i, /deep teal/i, /mustard/i, /\b7[0-9]\b/]) {
      expect(screen.queryByText(invented)).toBeNull();
    }
  });

  it('calls back on retry and dismiss', () => {
    const onRetry = jest.fn();
    const onDismiss = jest.fn();
    render(
      <AnalysisFailedState
        message="nope"
        allowancePreserved
        onRetry={onRetry}
        onDismiss={onDismiss}
      />
    );

    fireEvent.press(screen.getByTestId('retry'));
    fireEvent.press(screen.getByTestId('dismiss'));
    expect(onRetry).toHaveBeenCalled();
    expect(onDismiss).toHaveBeenCalled();
  });
});

describe('BetaFeatureUnavailableState', () => {
  it('uses private-beta language and offers nothing to buy', () => {
    render(
      <BetaFeatureUnavailableState message="Your invite gives you full access." />
    );

    expect(screen.getByText(/private beta/i)).toBeTruthy();
    expect(screen.queryByText(/go plus/i)).toBeNull();
    expect(screen.queryByText(/start monthly/i)).toBeNull();
    expect(screen.queryByText(/upgrade/i)).toBeNull();
  });
});

describe('DeletionConfirmState', () => {
  it('says plainly that deletion cannot be undone', () => {
    render(<DeletionConfirmState onConfirm={jest.fn()} onCancel={jest.fn()} />);
    expect(screen.getByText(/cannot be undone/i)).toBeTruthy();
  });

  it('requires an explicit confirmation', () => {
    const onConfirm = jest.fn();
    const onCancel = jest.fn();
    render(<DeletionConfirmState onConfirm={onConfirm} onCancel={onCancel} />);

    fireEvent.press(screen.getByTestId('cancel-delete'));
    expect(onCancel).toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();

    fireEvent.press(screen.getByTestId('confirm-delete'));
    expect(onConfirm).toHaveBeenCalled();
  });

  it('hides both buttons while the delete is in flight', () => {
    render(<DeletionConfirmState busy onConfirm={jest.fn()} onCancel={jest.fn()} />);
    expect(screen.queryByTestId('confirm-delete')).toBeNull();
    expect(screen.queryByTestId('cancel-delete')).toBeNull();
  });
});
