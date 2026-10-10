import { useQuery } from "@tanstack/react-query";
import { extensionStatus } from "@/lib/extensionsApi";
export function useExtensions() {
  return useQuery({ queryKey: ["extensions-ai"], queryFn: extensionStatus, retry: false, refetchInterval: 3000, staleTime: 1000 });
}
