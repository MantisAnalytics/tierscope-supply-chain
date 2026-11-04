import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Toaster } from "sonner";
import { AuthorProvider } from "./author";
import { ThemeProvider, useTheme } from "./theme";

// The API is always local, so the browser's online flag says nothing about whether it is reachable:
// "always" keeps queries and mutations running while the machine has no internet.
export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: 30_000, refetchOnWindowFocus: false, retry: 1, networkMode: "always" },
      mutations: { networkMode: "always" },
    },
  });
}

function ThemedToaster() {
  const { theme } = useTheme();
  return <Toaster theme={theme} position="bottom-right" richColors closeButton />;
}

export function Providers({ client, children }: { client: QueryClient; children: ReactNode }) {
  return (
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <AuthorProvider>
          {children}
          <ThemedToaster />
        </AuthorProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
