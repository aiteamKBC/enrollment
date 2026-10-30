import { Component, type ErrorInfo, type ReactNode } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';

// A render error inside the booking dialog used to propagate all the way up to
// the route-level RouteErrorBoundary, which replaces the ENTIRE page with its
// "This page stopped working" screen — so a single bad field blanked the whole
// workspace. This local boundary catches a throw from the booking form and
// surfaces it as a dismissible inline error instead, leaving the rest of the
// page (meeting lists, navigation, saved data) untouched. `resetKey` clears the
// error when a different booking is opened so a one-off failure is recoverable.

interface Props {
  children?: ReactNode;
  /** Changing this value (e.g. the booking session id) clears a prior error. */
  resetKey?: string;
  /** Dismiss the failed booking form. */
  onClose: () => void;
}

interface State {
  error: Error | null;
  resetKey?: string;
}

export class BookingErrorBoundary extends Component<Props, State> {
  state: State = { error: null, resetKey: this.props.resetKey };

  static getDerivedStateFromProps(props: Props, state: State) {
    return props.resetKey !== state.resetKey ? { error: null, resetKey: props.resetKey } : null;
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Booking form failed to render', error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children ?? null;

    return (
      <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm" onClick={this.props.onClose}>
        <div
          role="alertdialog"
          aria-label="Booking could not be opened"
          className="w-full max-w-md rounded-2xl bg-background-50 p-6 shadow-xl"
          onClick={(event) => event.stopPropagation()}
        >
          <div className="flex items-start gap-3">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-amber-100 text-amber-700">
              <AppIcon className="ri-error-warning-line text-lg"></AppIcon>
            </span>
            <div className="min-w-0">
              <h2 className="text-base font-heading font-bold text-foreground-950">This booking form could not be opened</h2>
              <p className="mt-1 text-[13px] text-foreground-600">
                Something in the booking form went wrong, so it was stopped instead of being left half-rendered.
                The rest of the page is unaffected and nothing you had saved has changed. Close this and try again.
              </p>
            </div>
          </div>
          <div className="mt-5 flex justify-end">
            <button
              type="button"
              onClick={this.props.onClose}
              className="inline-flex h-10 items-center gap-2 rounded-lg bg-primary-600 px-4 text-[13px] font-bold text-white transition-smooth hover:bg-primary-700"
            >
              <AppIcon className="ri-close-line"></AppIcon>
              Close
            </button>
          </div>
        </div>
      </div>
    );
  }
}
