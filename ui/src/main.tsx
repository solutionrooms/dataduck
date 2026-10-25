import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { MutationCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, consumeTokenFromHash } from "./api/client";
import { toast } from "./lib/toast";
import { App } from "./App";
import "./styles/theme.css";
import "./styles/layout.css";
import "./styles/components.css";

consumeTokenFromHash();

const queryClient = new QueryClient({
  mutationCache: new MutationCache({
    // Every failed mutation shows the API's message, unless the mutation handles errors itself.
    onError: (error, _vars, _ctx, mutation) => {
      if (mutation.options.onError) return;
      if (error instanceof ApiError && error.status === 401) return;
      toast.error(error);
    },
  }),
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      staleTime: 5_000,
      retry: (count, error) => {
        if (error instanceof ApiError && error.status > 0 && error.status < 500) return false;
        return count < 2;
      },
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
