import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import { Button } from "../primitives";
import styles from "./ErrorBoundary.module.css";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("ErrorBoundary caught:", error, info.componentStack);
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className={styles.errorContainer}>
          <AlertTriangle className={styles.icon} />
          <h2 className={styles.title}>Something went wrong</h2>
          {this.state.error?.message && (
            <p className={styles.message}>{this.state.error.message}</p>
          )}
          <Button variant="ghost" onClick={this.handleRetry}>
            Try Again
          </Button>
        </div>
      );
    }
    return this.props.children;
  }
}
