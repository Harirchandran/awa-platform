import { useParams, useNavigate } from 'react-router-dom';
import { ChatThread } from '@/features/conversations/components/ChatThread';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { ArrowLeft, User, Phone, Bot, MessagesSquare, LifeBuoy } from 'lucide-react';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { useConversation, type Conversation } from '@/features/conversations/api';
import { getConversationAiModeLabel, getConversationChannelLabel, getConversationStatusLabel } from '@/features/conversations/display';
import { format } from 'date-fns';
import { cn } from '@/lib/utils';

interface ProfileContentProps {
  conversation: Conversation | null | undefined;
  isLoading: boolean;
}

function ProfileContent({ conversation, isLoading }: ProfileContentProps) {
  if (isLoading) {
    return (
      <div className="space-y-2">
        <div className="h-4 bg-muted rounded w-1/2 animate-pulse" />
        <div className="h-4 bg-muted rounded w-3/4 animate-pulse" />
      </div>
    );
  }

  return (
    <>
      <div>
        <p className="text-[13px] font-medium text-foreground">{conversation?.customer_name || 'Anonymous'}</p>
        <p className="text-[12px] text-muted-foreground flex items-center gap-1 mt-0.5">
          <Phone className="w-3 h-3" /> {conversation?.customer_phone}
        </p>
      </div>
      <div className="space-y-2 pt-2 border-t border-border text-[12px]">
        <div className="flex justify-between items-center">
          <span className="text-muted-foreground">Status</span>
          <span className={cn(
            "rounded px-2 py-0.5 text-[11px] font-medium capitalize border",
            conversation?.status === 'escalated'
              ? "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-200/50 dark:border-amber-900/30"
              : conversation?.status === 'closed'
              ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-200/50 dark:border-emerald-900/30"
              : "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-200/50 dark:border-blue-900/30"
          )}>
            {getConversationStatusLabel(conversation?.status)}
          </span>
        </div>
        <div className="flex justify-between"><span className="text-muted-foreground">Channel</span><span className="text-foreground flex items-center gap-1"><MessagesSquare className="w-3 h-3" />{getConversationChannelLabel(conversation?.channel_type)}</span></div>
        <div className="flex justify-between"><span className="text-muted-foreground">Workflow</span><span className="uppercase text-foreground">{conversation?.workflow_state}</span></div>
        <div className="flex justify-between"><span className="text-muted-foreground">AI</span><span className="capitalize text-foreground flex items-center gap-1"><Bot className="w-3 h-3" />{getConversationAiModeLabel(conversation?.ai_mode)}</span></div>
        <div className="flex justify-between"><span className="text-muted-foreground">Started</span><span className="text-foreground">{conversation?.created_at ? format(new Date(conversation.created_at), 'MMM d, h:mm a') : '-'}</span></div>
      </div>
      <div className="space-y-2 border-t border-border pt-2 text-[12px] leading-5 text-muted-foreground">
        <div className="flex items-start gap-2 rounded-md border bg-background p-2">
          <LifeBuoy className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <p>
            Conversations is the full customer chat history. If AWA pauses for human help, the same thread appears in Needs Team Reply until it is resolved or returned to AWA.
          </p>
        </div>
        <p>
          AI mode shows whether automation is active for this thread. Manual reply and handoff actions are handled from Needs Team Reply when a chat is paused.
        </p>
      </div>
    </>
  );
}

export function ConversationPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: conversation, error, isError, isLoading, refetch } = useConversation(id);

  if (isError) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-4 p-6 text-center">
        <div className="max-w-md space-y-2">
          <h2 className="text-base font-semibold text-foreground">Could not open conversation</h2>
          <p className="text-sm leading-6 text-muted-foreground">
            {error?.message || 'The selected conversation could not be loaded. It may have been deleted or the API request failed.'}
          </p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Button variant="outline" onClick={() => navigate('/dashboard/conversations')}>
            <ArrowLeft className="h-4 w-4" />
            Back to conversations
          </Button>
          <Button onClick={() => refetch()}>Retry</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 space-y-4 flex flex-col h-full">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon" onClick={() => navigate('/dashboard/conversations')} className="h-7 w-7" aria-label="Back to conversations">
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <h2 className="text-[15px] font-semibold text-foreground">Conversation</h2>
        </div>
        {/* Mobile profile drawer */}
        {!isLoading && conversation && (
          <Sheet>
            <SheetTrigger asChild>
              <Button variant="outline" size="sm" className="lg:hidden h-8 text-[12px] flex items-center gap-1.5" aria-label="Open customer profile">
                <User className="w-3.5 h-3.5" /> Customer details
              </Button>
            </SheetTrigger>
            <SheetContent side="right" className="w-[300px] sm:w-[350px] overflow-y-auto">
              <SheetHeader className="pb-4 border-b">
                <SheetTitle className="text-[14px] font-bold text-foreground">Customer details</SheetTitle>
              </SheetHeader>
              <div className="mt-4 space-y-4">
                <ProfileContent conversation={conversation} isLoading={isLoading} />
              </div>
            </SheetContent>
          </Sheet>
        )}
      </div>
      <div className="flex flex-row gap-4 flex-1 min-h-0">
        <div className="hidden lg:block w-72 shrink-0">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-[12px] font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
                <User className="w-3.5 h-3.5" /> Profile
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <ProfileContent conversation={conversation} isLoading={isLoading} />
            </CardContent>
          </Card>
        </div>
        <div className="flex-1 rounded-lg overflow-hidden border border-border">
          <ChatThread />
        </div>
      </div>
    </div>
  );
}
