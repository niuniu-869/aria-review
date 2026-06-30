// ErrorBoundary — 隔离子树渲染异常，避免单个卡片崩溃拖垮整页。
import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}
interface State {
  hasError: boolean;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: unknown): void {
    // 仅记录，不上报；隔离即可
    console.error("ErrorBoundary 捕获渲染错误:", error);
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div className="state state-err">
            内容渲染出错（已隔离，不影响其他操作）
          </div>
        )
      );
    }
    return this.props.children;
  }
}
