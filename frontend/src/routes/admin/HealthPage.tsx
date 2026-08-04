import { useState, useCallback, useMemo } from 'react';
import { useOperationalHealth, fetchDLQItems, retryDLQ, purgeDLQ, signalWorkers, type ProviderHealth, type DLQItem } from '@/features/health/api';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from '@/components/ui/dialog';
import {
  ShieldCheck,
  ShieldAlert,
  Database,
  Server,
  RefreshCw,
  Activity,
  Cpu,
  PlugZap,
  Mail,
  Check,
  X,
  Key,
  Terminal,
  MessageSquare,
  CreditCard,
  Pause,
  Play,
  AlertTriangle,
  Inbox,
  RotateCcw,
  Trash2,
  Loader2,
} from 'lucide-react';
import { format } from 'date-fns';
import toast from 'react-hot-toast';
import { getApiErrorMessage } from '@/lib/api-errors';

function ProviderCard({ name, icon: Icon, data, type }: {
  name: string;
  icon: React.ElementType;
  data: ProviderHealth | undefined;
  type: string;
}) {
  if (!data) {
    return (
      <div className="rounded-md border border-border p-3 bg-muted/20 opacity-60">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Icon className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-medium">{name}</span>
          </div>
          <span className="text-xs bg-muted text-muted-foreground px-2 py-0.5 rounded">Unknown</span>
        </div>
      </div>
    );
  }

  const mode = data.mode || (type === 'llm' ? 'api' : 'unknown');
  const status = data.status || 'unknown';

  let statusLabel = status;
  let badgeStyle = 'bg-muted text-muted-foreground';

  if (status === 'ready' || status === 'configured' || status === 'live') {
    statusLabel = mode === 'live' ? 'Live' : status === 'configured' ? 'Configured' : 'Ready';
    badgeStyle = 'bg-emerald-500/10 text-emerald-600 border border-emerald-500/20';
  } else if (status === 'test_mode' || mode === 'test') {
    statusLabel = 'Test Mode';
    badgeStyle = 'bg-blue-500/10 text-blue-600 border border-blue-500/20';
  } else if (status === 'mock_mode' || mode === 'mock') {
    statusLabel = 'Simulated';
    badgeStyle = 'bg-purple-500/10 text-purple-600 border border-purple-500/20';
  } else if (status === 'disabled' || mode === 'disabled') {
    statusLabel = 'Disabled';
    badgeStyle = 'bg-muted text-muted-foreground border border-border';
  } else if (status === 'blocked') {
    statusLabel = 'Blocked';
    badgeStyle = 'bg-destructive/10 text-destructive border border-destructive/20';
  }

  const checks = data.checks || {};
  const checkEntries = Object.entries(checks);

  return (
    <div className={`rounded-md border p-3 ${
      status === 'blocked' ? 'border-destructive/30 bg-destructive/[0.02]' : 'border-border bg-card'
    }`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Icon className="h-4 w-4 text-muted-foreground animate-none" />
          <span className="text-sm font-semibold">{name}</span>
        </div>
        <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${badgeStyle}`}>
          {statusLabel.toUpperCase()}
        </span>
      </div>

      {checkEntries.length > 0 && (
        <div className="mt-2.5 space-y-1 border-t border-border/50 pt-2 text-[11px] text-muted-foreground">
          {checkEntries.map(([key, val]) => {
            const label = key.replace(/_/g, ' ');
            const isOk = Boolean(val);
            return (
              <div key={key} className="flex items-center justify-between">
                <span className="capitalize">{label}</span>
                <span className="flex items-center">
                  {isOk ? (
                    <Check className="w-3 h-3 text-emerald-500 mr-0.5" />
                  ) : (
                    <X className="w-3 h-3 text-destructive mr-0.5" />
                  )}
                  {typeof val === 'number' ? val : isOk ? 'Yes' : 'No'}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {type === 'llm' && (
        <div className="mt-2.5 space-y-1 border-t border-border/50 pt-2 text-[11px] text-muted-foreground font-mono">
          <div className="flex justify-between">
            <span>Agent LLM:</span>
            <span className="text-foreground">{data.agent_provider || 'unknown'}</span>
          </div>
          <div className="flex justify-between">
            <span>Router LLM:</span>
            <span className="text-foreground">{data.routing_provider || 'unknown'}</span>
          </div>
        </div>
      )}
    </div>
  );
}

export function HealthPage() {
  const { data: health, isLoading, isError, isFetching, refetch } = useOperationalHealth();
  const isHealthy = health?.status === 'healthy';

  // ---- Worker Pause State ----
  const [workersPaused, setWorkersPaused] = useState(false);
  const [pauseLoading, setPauseLoading] = useState(false);

  const handleWorkerSignal = useCallback(async () => {
    const action = workersPaused ? 'resume' : 'pause';
    setPauseLoading(true);
    try {
      const result = await signalWorkers(action);
      setWorkersPaused(result.paused);
      toast.success(result.paused ? 'All workers paused.' : 'Workers resumed.');
    } catch (err) {
      toast.error(`Worker signal failed: ${getApiErrorMessage(err)}`);
    } finally {
      setPauseLoading(false);
    }
  }, [workersPaused]);

  // ---- DLQ Modal State ----
  const [dlqOpen, setDlqOpen] = useState(false);
  const [dlqItems, setDlqItems] = useState<DLQItem[]>([]);
  const [dlqTotal, setDlqTotal] = useState(0);
  const [dlqLoading, setDlqLoading] = useState(false);
  const [dlqActionLoading, setDlqActionLoading] = useState<'retry' | 'purge' | null>(null);

  const openDLQ = useCallback(async () => {
    setDlqOpen(true);
    setDlqLoading(true);
    try {
      const data = await fetchDLQItems();
      setDlqItems(data.items);
      setDlqTotal(data.total);
    } catch (err) {
      toast.error(`Failed to load DLQ: ${getApiErrorMessage(err)}`);
    } finally {
      setDlqLoading(false);
    }
  }, []);

  const handleRetryAll = useCallback(async () => {
    setDlqActionLoading('retry');
    try {
      const result = await retryDLQ('all');
      toast.success(`${result.retried} item(s) moved back to the main queue.`);
      setDlqItems([]);
      setDlqTotal(0);
      void refetch();
    } catch (err) {
      toast.error(`Retry failed: ${getApiErrorMessage(err)}`);
    } finally {
      setDlqActionLoading(null);
    }
  }, [refetch]);

  const handlePurge = useCallback(async () => {
    if (!window.confirm(`Permanently delete all ${dlqTotal} DLQ item(s)?\n\nThis cannot be undone.`)) return;
    setDlqActionLoading('purge');
    try {
      const result = await purgeDLQ();
      toast.success(`DLQ cleared — ${result.purged} item(s) deleted.`);
      setDlqItems([]);
      setDlqTotal(0);
      void refetch();
    } catch (err) {
      toast.error(`Purge failed: ${getApiErrorMessage(err)}`);
    } finally {
      setDlqActionLoading(null);
    }
  }, [dlqTotal, refetch]);

  // derived data
  const workerList = useMemo(() => {
    if (!health) return [];
    const list = [];

    // 1. Main Worker
    if (health.workers?.main) {
      list.push({
        key: 'main',
        name: 'Main Worker',
        status: health.workers.main.status,
        lag: health.workers.main.lag_seconds,
        ttl: health.workers.main.ttl_seconds,
        lastSeen: health.workers.main.last_seen_at,
      });
    } else {
      list.push({
        key: 'main',
        name: 'Main Worker',
        status: 'missing',
        lag: null,
        ttl: 15,
        lastSeen: null,
      });
    }

    // 2. Burst Worker
    if (health.workers?.burst) {
      list.push({
        key: 'burst',
        name: 'Burst Worker',
        status: health.workers.burst.status,
        lag: health.workers.burst.lag_seconds,
        ttl: health.workers.burst.ttl_seconds,
        lastSeen: health.workers.burst.last_seen_at,
      });
    } else {
      list.push({
        key: 'burst',
        name: 'Burst Worker',
        status: 'missing',
        lag: null,
        ttl: 15,
        lastSeen: null,
      });
    }

    // 3. Reminder Scheduler
    if (health.workers?.reminder_scheduler) {
      list.push({
        key: 'reminder_scheduler',
        name: 'Reminder Scheduler',
        status: health.workers.reminder_scheduler.status,
        lag: health.workers.reminder_scheduler.lag_seconds,
        ttl: health.workers.reminder_scheduler.ttl_seconds,
        lastSeen: health.workers.reminder_scheduler.last_seen_at,
      });
    } else {
      list.push({
        key: 'reminder_scheduler',
        name: 'Reminder Scheduler',
        status: 'missing',
        lag: null,
        ttl: 60,
        lastSeen: null,
      });
    }

    // 4. Event Dispatcher
    if (health.events?.dispatcher) {
      list.push({
        key: 'event_dispatcher',
        name: 'Event Dispatcher',
        status: health.events.dispatcher.status,
        lag: health.events.dispatcher.lag_seconds,
        ttl: health.events.dispatcher.ttl_seconds,
        lastSeen: health.events.dispatcher.last_seen_at,
        lastSuccess: health.events.dispatcher.last_success_at,
        lastError: health.events.dispatcher.last_error_at,
      });
    } else {
      list.push({
        key: 'event_dispatcher',
        name: 'Event Dispatcher',
        status: 'missing',
        lag: null,
        ttl: 30,
        lastSeen: null,
      });
    }

    return list;
  }, [health]);

  const checkedAtStr = useMemo(() => {
    if (!health?.checked_at) return '';
    try {
      return format(new Date(health.checked_at), 'HH:mm:ss');
    } catch {
      return '';
    }
  }, [health]);

  return (
    <div className="space-y-6">

      {/* DLQ Inspection Dialog */}
      <Dialog open={dlqOpen} onOpenChange={setDlqOpen}>
        <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Inbox className="h-4 w-4 text-destructive" />
              Dead-Letter Queue Inspector
            </DialogTitle>
            <DialogDescription>
              {dlqTotal > 0
                ? `${dlqTotal} failed message(s) — retry to requeue or purge to discard permanently.`
                : 'The DLQ is empty.'}
            </DialogDescription>
          </DialogHeader>

          <div className="flex-1 overflow-y-auto min-h-0 space-y-2 pr-1">
            {dlqLoading && (
              <div className="flex items-center justify-center py-10 text-muted-foreground">
                <Loader2 className="h-5 w-5 animate-spin mr-2" /> Loading DLQ items...
              </div>
            )}
            {!dlqLoading && dlqItems.length === 0 && (
              <p className="text-center py-10 text-sm text-muted-foreground">No items in the DLQ.</p>
            )}
            {!dlqLoading && dlqItems.map((item) => (
              <div key={item.index} className="rounded-md border border-destructive/20 bg-destructive/[0.02] p-3">
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-xs font-mono text-muted-foreground">Item #{item.index + 1}</span>
                  {'tenant_id' in item.payload && (
                    <span className="text-[10px] bg-muted px-1.5 py-0.5 rounded font-mono">
                      tenant: {String(item.payload.tenant_id).slice(0, 8)}…
                    </span>
                  )}
                </div>
                <pre className="text-[10px] font-mono text-foreground/70 overflow-x-auto whitespace-pre-wrap break-all bg-muted/30 rounded p-2">
                  {JSON.stringify(item.payload, null, 2)}
                </pre>
              </div>
            ))}
          </div>

          {dlqTotal > 0 && (
            <DialogFooter className="gap-2 flex-row justify-end border-t border-border pt-4">
              <Button
                variant="outline"
                size="sm"
                onClick={handleRetryAll}
                disabled={dlqActionLoading !== null}
              >
                {dlqActionLoading === 'retry'
                  ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
                  : <RotateCcw className="h-3.5 w-3.5 mr-1.5" />}
                Retry All
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={handlePurge}
                disabled={dlqActionLoading !== null}
              >
                {dlqActionLoading === 'purge'
                  ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
                  : <Trash2 className="h-3.5 w-3.5 mr-1.5" />}
                Purge DLQ
              </Button>
            </DialogFooter>
          )}
        </DialogContent>
      </Dialog>

      {/* Workers Paused Banner */}
      {workersPaused && (
        <div className="flex items-center gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 animate-pulse">
          <AlertTriangle className="h-5 w-5 text-amber-500 shrink-0" />
          <div>
            <p className="text-sm font-semibold text-amber-700 dark:text-amber-400">All Workers Paused</p>
            <p className="text-xs text-amber-600/80 dark:text-amber-500/80">
              Message processing is suspended. Inbound messages are queued but not dispatched.
            </p>
          </div>
        </div>
      )}

      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Platform Health</h1>
          <p className="text-muted-foreground mt-1">
            Real-time status of backend services and message processing queues.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {checkedAtStr && (
            <span className="text-xs text-muted-foreground mr-1">
              Refreshed: {checkedAtStr}
            </span>
          )}
          <Button
            variant={workersPaused ? 'default' : 'outline'}
            size="sm"
            onClick={handleWorkerSignal}
            disabled={pauseLoading}
            className={`h-8 gap-1.5 ${
              workersPaused
                ? 'bg-amber-500 hover:bg-amber-600 text-white border-amber-500'
                : ''
            }`}
          >
            {pauseLoading
              ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
              : workersPaused
                ? <Play className="w-3.5 h-3.5" />
                : <Pause className="w-3.5 h-3.5" />}
            {workersPaused ? 'Resume Workers' : 'Pause Workers'}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => refetch()}
            disabled={isFetching}
            className="h-8 gap-1.5"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
          <div className="text-xs text-muted-foreground bg-muted/50 px-2 py-1 rounded-md">
            Auto: 30s
          </div>
        </div>
      </div>

      {isError && (
        <div className="bg-destructive/10 border border-destructive p-4 rounded-lg flex items-start text-destructive">
          <ShieldAlert className="w-5 h-5 mr-3 mt-0.5" />
          <div>
            <h3 className="font-medium">Failed to reach telemetry servers</h3>
            <p className="text-sm mt-1 opacity-90">The FastAPI backend may be offline entirely.</p>
          </div>
        </div>
      )}

      <Card className={`border-t-4 ${isHealthy ? 'border-t-emerald-500' : 'border-t-destructive'}`}>
        <CardContent className="pt-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center">
              <div className={`p-3 rounded-full mr-4 ${isHealthy ? 'bg-emerald-500/10 text-emerald-500' : 'bg-destructive/10 text-destructive'}`}>
                {isHealthy ? <ShieldCheck className="h-6 w-6" /> : <ShieldAlert className="h-6 w-6" />}
              </div>
              <div>
                <h2 className="text-2xl font-bold">
                  {isLoading ? 'Checking...' : isHealthy ? 'All Systems Operational' : 'Degraded Performance'}
                </h2>
                <p className="text-muted-foreground text-sm mt-1">
                  Version: {health?.version || '--'}
                </p>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {/* DATABASE */}
        <Card>
          <CardHeader className="pb-2 flex flex-row items-center justify-between">
            <CardTitle className="text-sm font-medium text-muted-foreground uppercase tracking-wider">
              PostgreSQL
            </CardTitle>
            <Database className="w-4 h-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="flex items-center mt-2">
              {health?.services?.database === 'healthy' ? (
                <div className="w-3 h-3 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.5)] mr-3" />
              ) : (
                <div className="w-3 h-3 rounded-full bg-destructive shadow-[0_0_8px_rgba(239,68,68,0.5)] mr-3" />
              )}
              <span className="text-2xl font-bold uppercase">{isLoading ? '---' : health?.services?.database || 'ERR'}</span>
            </div>
            <CardDescription className="mt-2">Primary datastore</CardDescription>
          </CardContent>
        </Card>

        {/* REDIS */}
        <Card>
          <CardHeader className="pb-2 flex flex-row items-center justify-between">
            <CardTitle className="text-sm font-medium text-muted-foreground uppercase tracking-wider">
              Redis Cache
            </CardTitle>
            <Server className="w-4 h-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="flex items-center mt-2">
              {health?.services?.redis === 'healthy' ? (
                <div className="w-3 h-3 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.5)] mr-3" />
              ) : (
                <div className="w-3 h-3 rounded-full bg-destructive shadow-[0_0_8px_rgba(239,68,68,0.5)] mr-3" />
              )}
              <span className="text-2xl font-bold uppercase">{isLoading ? '---' : health?.services?.redis || 'ERR'}</span>
            </div>
            <CardDescription className="mt-2">Session state & caching</CardDescription>
          </CardContent>
        </Card>

        {/* WORKER QUEUE */}
        <Card>
          <CardHeader className="pb-2 flex flex-row items-center justify-between">
            <CardTitle className="text-sm font-medium text-muted-foreground uppercase tracking-wider">
              Queue Depth
            </CardTitle>
            <Activity className="w-4 h-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="flex items-center mt-2">
              <div className={`w-3 h-3 rounded-full mr-3 ${
                health?.queue_alert === 'ok' ? 'bg-emerald-500' : 'bg-amber-500'
              }`} />
              <span className="text-2xl font-bold">{isLoading ? '---' : health?.queue_depth ?? 'ERR'}</span>
            </div>
            <CardDescription className="mt-2">Pending AI workflows</CardDescription>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* WORKERS STATUS */}
        <Card className="lg:col-span-1">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Cpu className="h-4 w-4" />
              Workers
            </CardTitle>
            <CardDescription>Main, burst, scheduler, and dispatcher heartbeats</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {isLoading && (
              <p className="text-sm text-muted-foreground animate-pulse">Loading worker statuses...</p>
            )}
            {!isLoading && workerList.length === 0 && (
              <p className="text-sm text-muted-foreground">No worker telemetry available.</p>
            )}
            {!isLoading && workerList.map((worker) => {
              const isLagging = typeof worker.lag === 'number' && worker.lag > 30;
              const isStale = typeof worker.lag === 'number' && worker.lag > worker.ttl;
              const isOffline = worker.status === 'unhealthy' || worker.status === 'missing' || isStale;

              let badgeText = 'Unknown';
              let badgeStyle = 'bg-muted text-muted-foreground';

              if (isOffline) {
                badgeText = worker.status === 'missing' ? 'Missing' : 'Offline';
                badgeStyle = 'bg-destructive/10 text-destructive border border-destructive/20';
              } else if (isLagging) {
                badgeText = `Lagging (${worker.lag}s)`;
                badgeStyle = 'bg-amber-500/10 text-amber-600 border border-amber-500/20';
              } else if (worker.status === 'healthy') {
                badgeText = 'Healthy';
                badgeStyle = 'bg-emerald-500/10 text-emerald-600 border border-emerald-500/20';
              }

              return (
                <div key={worker.key} className={`flex flex-col gap-1 rounded-md border p-3 ${
                  isOffline ? 'border-destructive/30 bg-destructive/[0.02]' : isLagging ? 'border-amber-500/30 bg-amber-500/[0.01]' : 'border-border'
                }`}>
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm font-semibold text-foreground">{worker.name}</p>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        {typeof worker.lag === 'number' ? `Heartbeat: ${worker.lag}s ago` : 'No recent heartbeat'}
                      </p>
                    </div>
                    <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${badgeStyle}`}>
                      {badgeText}
                    </span>
                  </div>
                  {worker.key === 'event_dispatcher' && (worker.lastSuccess || worker.lastError) && (
                    <div className="mt-2 pt-2 border-t border-dashed border-border flex flex-col gap-0.5 text-[10px] text-muted-foreground font-mono">
                      {worker.lastSuccess && (
                        <div>Last success: {format(new Date(worker.lastSuccess), 'HH:mm:ss')}</div>
                      )}
                      {worker.lastError && (
                        <div className="text-destructive">Last error: {format(new Date(worker.lastError), 'HH:mm:ss')}</div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </CardContent>
        </Card>

        {/* QUEUE AND PROVIDER TELEMETRY */}
        <div className="lg:col-span-2 space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Activity className="h-4 w-4" />
                Queue Signals
              </CardTitle>
              <CardDescription>Backlog, processing, and dead-letter queue depth</CardDescription>
            </CardHeader>
            <CardContent className="grid grid-cols-3 gap-3">
              <div className="rounded-md border border-border p-3">
                <p className="text-xs text-muted-foreground">Queued</p>
                <p className="mt-1 text-xl font-semibold">{health?.queue_depth ?? '--'}</p>
              </div>
              <div className="rounded-md border border-border p-3">
                <p className="text-xs text-muted-foreground">Processing</p>
                <p className="mt-1 text-xl font-semibold">{health?.processing_depth ?? '--'}</p>
              </div>
              {/* DLQ box — clickable to open inspector */}
              <button
                type="button"
                onClick={openDLQ}
                className={`rounded-md border p-3 text-left transition-colors cursor-pointer hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                  (health?.dlq_depth || 0) > 0
                    ? 'border-destructive bg-destructive/5 text-destructive font-medium'
                    : 'border-border hover:border-muted-foreground/30'
                }`}
              >
                <p className={`text-xs ${
                  (health?.dlq_depth || 0) > 0 ? 'text-destructive font-semibold' : 'text-muted-foreground'
                }`}>DLQ</p>
                <p className="mt-1 text-xl font-bold">{health?.dlq_depth ?? '--'}</p>
                {(health?.dlq_depth || 0) > 0 ? (
                  <div className="mt-1.5 flex items-center text-[10px] font-semibold text-destructive animate-pulse">
                    <ShieldAlert className="w-3 h-3 mr-1" />
                    Click to Inspect
                  </div>
                ) : (
                  <div className="mt-1.5 flex items-center text-[10px] text-muted-foreground">
                    <Inbox className="w-3 h-3 mr-1" />
                    View Items
                  </div>
                )}
              </button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Terminal className="h-4 w-4" />
                Event Store Signals
              </CardTitle>
              <CardDescription>Processed, failed, and skipped business events</CardDescription>
            </CardHeader>
            <CardContent className="grid grid-cols-3 gap-3">
              <div className="rounded-md border border-border p-3">
                <p className="text-xs text-muted-foreground">Processed</p>
                <p className="mt-1 text-xl font-semibold">{health?.events?.counts?.processed ?? '--'}</p>
              </div>
              <div className="rounded-md border border-border p-3">
                <p className="text-xs text-muted-foreground">Failed</p>
                <p className="mt-1 text-xl font-semibold">{health?.events?.counts?.failed ?? '--'}</p>
              </div>
              <div className="rounded-md border border-border p-3">
                <p className="text-xs text-muted-foreground">Skipped</p>
                <p className="mt-1 text-xl font-semibold">{health?.events?.counts?.skipped ?? '--'}</p>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <PlugZap className="h-4 w-4 text-muted-foreground" />
                Provider Integration Readiness
              </CardTitle>
              <CardDescription>Real-time status of external API configurations and readiness</CardDescription>
            </CardHeader>
            <CardContent className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <ProviderCard
                name="WhatsApp API"
                icon={MessageSquare}
                data={health?.providers?.whatsapp}
                type="whatsapp"
              />
              <ProviderCard
                name="Razorpay Payments"
                icon={CreditCard}
                data={health?.providers?.razorpay}
                type="razorpay"
              />
              <ProviderCard
                name="Cloudflare Turnstile CAPTCHA"
                icon={ShieldCheck}
                data={health?.providers?.turnstile}
                type="turnstile"
              />
              <ProviderCard
                name="Customer SMS/OTP Webhook"
                icon={Key}
                data={health?.providers?.otp}
                type="otp"
              />
              <ProviderCard
                name="SMTP / Email Invites"
                icon={Mail}
                data={health?.providers?.smtp_email}
                type="smtp_email"
              />
              <ProviderCard
                name="LLM Provider Configuration"
                icon={Terminal}
                data={health?.providers?.llm}
                type="llm"
              />
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}