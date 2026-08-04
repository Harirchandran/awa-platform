import { useState } from 'react';
import { usePrompts, useUpdatePrompt } from '@/features/ai/api';
import Editor from '@monaco-editor/react';
import { ShieldAlert, TerminalSquare, Save, History } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import { format } from 'date-fns';
export function PromptsPage() {
  const { data: prompts, isLoading, isError } = usePrompts();
  const { mutate: savePrompt, isPending: isSaving } = useUpdatePrompt();
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [draftsByKey, setDraftsByKey] = useState<Record<string, string>>({});
  const selectedPrompt = prompts?.find((prompt) => prompt.key === selectedKey) ?? prompts?.[0];
  const editorContent = selectedPrompt ? draftsByKey[selectedPrompt.key] ?? selectedPrompt.body : '';
  const hasChanges = Boolean(selectedPrompt && selectedPrompt.body !== editorContent);
  const handleSelectPrompt = (key: string) => {
    setSelectedKey(key);
  };
  const handleEditorChange = (value: string) => {
    if (!selectedPrompt) {
      return;
    }
    setDraftsByKey((current) => ({
      ...current,
      [selectedPrompt.key]: value,
    }));
  };
  const handleSave = () => {
    if (selectedPrompt) {
      savePrompt({ key: selectedPrompt.key, body: editorContent });
    }
  };
  return (
    <div className="flex flex-col h-full h-[calc(100vh-200px)]">
      <div className="mb-6 shrink-0 flex flex-col sm:flex-row justify-between items-start sm:items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">AI Prompts Registry</h1>
          <p className="text-muted-foreground mt-1 text-base">
            Master control for LangChain system instructions. Changes apply globally to all tenants.
          </p>
        </div>
        <div className="mt-4 sm:mt-0 flex items-center space-x-3 text-sm text-amber-500 bg-amber-500/10 px-4 py-2 rounded-md">
            <ShieldAlert className="w-4 h-4 shrink-0" />
            <span className="font-medium">Direct Template Injection</span>
        </div>
      </div>
      {isError ? (
        <div className="bg-destructive/10 border border-destructive p-4 rounded-lg flex items-start text-destructive h-32">
          <ShieldAlert className="w-5 h-5 mr-3 mt-0.5" />
          <div>
            <h3 className="font-medium">Failed to fetch prompt templates</h3>
            <p className="text-sm mt-1 opacity-90">The database containing system instructions may be unreachable.</p>
          </div>
        </div>
      ) : isLoading ? (
        <div className="flex min-h-[500px] flex-1 overflow-hidden rounded-lg border border-border">
          <div className="w-1/3 border-r border-border bg-muted/20 p-4 space-y-4">
             <Skeleton className="h-[60px] w-full rounded-lg" />
             <Skeleton className="h-[60px] w-full rounded-lg" />
             <Skeleton className="h-[60px] w-full rounded-lg" />
          </div>
          <div className="w-2/3 bg-[#1e1e1e] p-6">
             <Skeleton className="h-4 w-3/4 bg-[#2d2d2d] mb-4" />
             <Skeleton className="h-4 w-1/2 bg-[#2d2d2d] mb-4" />
             <Skeleton className="h-4 w-5/6 bg-[#2d2d2d]" />
          </div>
        </div>
      ) : (
        <div className="flex min-h-[500px] flex-1 flex-col overflow-hidden rounded-lg border border-border bg-card shadow-sm lg:flex-row">
          {/* Left Panel: List of Prompts */}
          <div className="flex max-h-64 flex-col border-b border-border lg:max-h-none lg:w-1/3 lg:border-b-0 lg:border-r">
            <div className="p-4 border-b border-border bg-muted/30">
                <h3 className="font-semibold flex items-center">
                    <TerminalSquare className="w-4 h-4 mr-2 text-indigo-500" />
                    Registry Matrix
                </h3>
            </div>
            <ScrollArea className="flex-1">
                <div className="p-2 space-y-1">
                    {prompts?.map((prompt) => (
                        <button
                            key={prompt.key}
                            onClick={() => handleSelectPrompt(prompt.key)}
                            className={cn(
                                "w-full text-left px-4 py-3 rounded-md transition-colors flex flex-col",
                                selectedPrompt?.key === prompt.key
                                    ? "bg-primary/10 text-primary"
                                    : "hover:bg-muted text-muted-foreground"
                            )}
                        >
                            <span className="font-medium font-mono text-sm mb-1">{prompt.key}</span>
                            <span className="text-xs opacity-80 truncate">{prompt.description}</span>
                        </button>
                    ))}
                    {(!prompts || prompts.length === 0) && (
                        <div className="text-center p-6 text-muted-foreground text-sm">
                            No prompt templates found.
                        </div>
                    )}
                </div>
            </ScrollArea>
          </div>
          {/* Right Panel: Monaco Editor */}
          <div className="flex min-h-[420px] flex-1 flex-col bg-[#1e1e1e] lg:w-2/3">
            {selectedPrompt ? (
                <>
                    <div className="h-12 bg-[#252526] border-b border-[#3c3c3c] flex items-center justify-between px-4 shrink-0 text-gray-300">
                        <div className="flex items-center">
                            <span className="text-sm font-mono tracking-wider text-indigo-300">{selectedPrompt.key}.txt</span>
                        </div>
                        <div className="flex items-center space-x-4 text-xs">
                            <span className="flex items-center text-gray-400">
                                <History className="w-3 h-3 mr-1" />
                                v{selectedPrompt.version} &bull; {format(new Date(selectedPrompt.updated_at), 'MMM d, yy')}
                            </span>
                        </div>
                    </div>
                    <div className="flex-1 relative">
                        <Editor
                            height="100%"
                            language="plaintext"
                            theme="vs-dark"
                            value={editorContent}
                            onChange={(val) => handleEditorChange(val || '')}
                            options={{
                                minimap: { enabled: false },
                                fontSize: 13,
                                fontFamily: "'JetBrains Mono', monospace",
                                wordWrap: "on",
                                padding: { top: 16, bottom: 16 },
                                lineNumbersMinChars: 3,
                                scrollBeyondLastLine: false,
                            }}
                        />
                    </div>
                    <div className="h-16 bg-[#252526] border-t border-[#3c3c3c] flex items-center justify-between px-6 shrink-0">
                        <div>
                           {hasChanges && <span className="text-amber-400 text-sm font-medium">Unsaved changes</span>}
                        </div>
                        <Button
                            onClick={handleSave}
                            disabled={!hasChanges || isSaving}
                            className="bg-indigo-600 hover:bg-indigo-700 text-white"
                        >
                            <Save className={`w-4 h-4 mr-2 ${isSaving ? 'animate-pulse' : ''}`} />
                            {isSaving ? 'Saving...' : 'Deploy Template'}
                        </Button>
                    </div>
                </>
            ) : (
                <div className="flex-1 flex items-center justify-center text-gray-500">
                    Select a prompt template to edit.
                </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}