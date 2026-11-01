"use client";

import { RefreshCwIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/core/auth/AuthProvider";

export function HomeGatewayUnavailable() {
  const router = useRouter();
  const { user } = useAuth();

  useEffect(() => {
    if (user !== null) {
      router.refresh();
    }
  }, [router, user]);

  return (
    <div className="flex h-screen flex-col items-center justify-center gap-4 px-4 text-center">
      <p className="text-muted-foreground">Service temporarily unavailable.</p>
      <Button type="button" variant="outline" onClick={() => router.refresh()}>
        <RefreshCwIcon />
        Retry
      </Button>
    </div>
  );
}
