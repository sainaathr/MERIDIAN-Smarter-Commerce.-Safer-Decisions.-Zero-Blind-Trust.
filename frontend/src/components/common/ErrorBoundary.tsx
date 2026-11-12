import { Component, type ErrorInfo, type ReactNode } from 'react';
import { AlertTriangle, RotateCcw } from 'lucide-react';
import { MeridianLogo } from './MeridianLogo';

interface Props {
  children: ReactNode;
  fallbackTitle?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('ErrorBoundary caught an unhandled error:', error, errorInfo);
  }

  private handleReset = () => {
    this.setState({ hasError: false, error: null });
  };

  public render() {
    if (this.state.hasError) {
      return (
        <div className="p-8 m-4 rounded-2xl bg-[#171717] border border-[#F04400] shadow-2xl text-center space-y-4 max-w-lg mx-auto mt-12">
          <div className="flex flex-col items-center justify-center gap-2">
            <MeridianLogo variant="icon" size="md" className="mx-auto" />
            <div className="w-10 h-10 rounded-xl bg-[#F04400] flex items-center justify-center mx-auto text-black shadow-lg">
              <AlertTriangle className="w-5 h-5 stroke-[2.5]" />
            </div>
          </div>
          <div>
            <h3 className="text-lg font-black text-white font-mono uppercase tracking-tight">
              {this.props.fallbackTitle || 'Component Error'}
            </h3>
            <p className="text-xs text-[#A3A3A3] font-mono mt-1 leading-relaxed">
              {this.state.error?.message || 'An unexpected rendering error occurred in this view.'}
            </p>
          </div>
          <button
            onClick={this.handleReset}
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-[#F04400] hover:bg-[#ff5714] text-black text-xs font-black font-mono uppercase tracking-wider transition shadow-md"
          >
            <RotateCcw className="w-4 h-4" />
            <span>Reset Component State</span>
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
