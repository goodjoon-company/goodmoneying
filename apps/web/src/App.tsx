import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { OperationsConsole } from "./components/OperationsConsole";

export function App() {
  const [queryClient] = useState(() => new QueryClient());

  return (
    <QueryClientProvider client={queryClient}>
      <OperationsConsole />
    </QueryClientProvider>
  );
}
