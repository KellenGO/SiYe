import { useQuery } from "@tanstack/react-query";
import { extensionStatus } from "@/lib/extensionsApi";
export function useExtensions() {
  return useQuery({ queryKey: ["extensions-ai"], queryFn: extensionStatus, retry: false,
    // 正常时 3 秒一探；本机服务出错时降到 30 秒，别拿失败请求空转。
    refetchInterval: (query) => (query.state.error ? 30000 : 3000), staleTime: 1000 });
}
