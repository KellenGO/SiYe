export interface EnvironmentHealth {
  status: "ok";
  environment_status: "ok" | "degraded";
  backend_available: boolean;
  version: string;
  api_version: string;
  web_version: string | null;
  version_match: boolean | null;
  browser_available: boolean;
  browser_backend: string | null;
  redis_required: boolean;
  redis_available: boolean | null;
}

/** Keep the frontend/API compatibility check separate from backend liveness. */
export function environmentHealthWarning(
  health: EnvironmentHealth | false | null,
): string | null {
  if (health === false) return "四野服务未连接，请重新启动应用。";
  if (!health) return null;
  if (health.version_match === false || typeof health.version !== "string") {
    return "应用文件版本不一致，请重新安装或更新四野。";
  }
  if (health.browser_available === false) {
    return "没有找到可用的浏览器，请安装 Chrome 或 Edge 后重新启动四野。";
  }
  if (health.redis_required && health.redis_available === false) {
    return "四野暂时无法连接所需的本机服务，请稍后重试。";
  }
  return null;
}
