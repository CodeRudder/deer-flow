"use client";

import { BotIcon, MessageSquareIcon, PenLineIcon, PlusIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useAgents } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";

import { AgentCard } from "./agent-card";
import { AgentCreateOption, AgentCreateSheet } from "./agent-create-sheet";

export function AgentGallery() {
  const { t } = useI18n();
  const { agents, isLoading } = useAgents();
  const router = useRouter();
  const [choiceOpen, setChoiceOpen] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);

  const handleNewAgent = () => {
    setChoiceOpen(true);
  };

  const handleManualCreate = () => {
    setChoiceOpen(false);
    setManualOpen(true);
  };

  const handleChatCreate = () => {
    setChoiceOpen(false);
    router.push("/workspace/agents/new");
  };

  return (
    <div className="flex size-full flex-col">
      {/* Page header */}
      <div className="flex items-center justify-between border-b px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold">{t.agents.title}</h1>
          <p className="text-muted-foreground mt-0.5 text-sm">
            {t.agents.description}
          </p>
        </div>
        <Button onClick={handleNewAgent}>
          <PlusIcon className="mr-1.5 h-4 w-4" />
          {t.agents.newAgent}
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {isLoading ? (
          <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">
            {t.common.loading}
          </div>
        ) : agents.length === 0 ? (
          <div className="flex h-64 flex-col items-center justify-center gap-3 text-center">
            <div className="bg-muted flex h-14 w-14 items-center justify-center rounded-full">
              <BotIcon className="text-muted-foreground h-7 w-7" />
            </div>
            <div>
              <p className="font-medium">{t.agents.emptyTitle}</p>
              <p className="text-muted-foreground mt-1 text-sm">
                {t.agents.emptyDescription}
              </p>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {agents.map((agent) => (
              <AgentCard key={agent.name} agent={agent} />
            ))}
          </div>
        )}
      </div>

      {/* New-agent choice: manual form vs. chat-based bootstrap flow */}
      <Dialog open={choiceOpen} onOpenChange={setChoiceOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t.agents.newAgent}</DialogTitle>
            <DialogDescription>
              {t.agents.createChoiceDescription}
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2">
            <AgentCreateOption
              icon={<PenLineIcon className="h-4 w-4" />}
              title={t.agents.createChoiceManualTitle}
              description={t.agents.createChoiceManualDescription}
              onClick={handleManualCreate}
            />
            <AgentCreateOption
              icon={<MessageSquareIcon className="h-4 w-4" />}
              title={t.agents.createChoiceChatTitle}
              description={t.agents.createChoiceChatDescription}
              onClick={handleChatCreate}
            />
          </div>
        </DialogContent>
      </Dialog>

      <AgentCreateSheet open={manualOpen} onOpenChange={setManualOpen} />
    </div>
  );
}
