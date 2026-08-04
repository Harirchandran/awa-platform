/* eslint-disable react-hooks/set-state-in-effect */
import { useState, useEffect, useCallback } from 'react';
import {
  useScheduleErasure,
  useCancelErasure,
  useExportCustomer,
  fetchCustomerStatus,
} from '@/features/privacy/api';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  ShieldAlert, Trash2, Download, FileJson, AlertTriangle,
  CheckCircle2, Info, Clock, Ban, Loader2,
} from 'lucide-react';

interface PendingErasureState {
  erasure_scheduled_at: string;
}

export function PrivacyPage() {
  const [customerId, setCustomerId] = useState('');
  const [confirmText, setConfirmText] = useState('');
  const [exportData, setExportData] = useState<Record<string, unknown> | null>(null);
  const [pendingErasure, setPendingErasure] = useState<PendingErasureState | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);

  const { mutate: scheduleErasure, isPending: isScheduling } = useScheduleErasure();
  const { mutate: cancelErasure, isPending: isCancelling } = useCancelErasure();
  const { mutate: exportCustomer, isPending: isExporting } = useExportCustomer();

  const isValidUUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(customerId);

  // Whenever a valid UUID is entered, check if the customer is already pending_erasure.
  const checkStatus = useCallback(async (id: string) => {
    setStatusLoading(true);
    try {
      const result = await fetchCustomerStatus(id);
      if (result.status === 'pending_erasure' && result.erasure_scheduled_at) {
        setPendingErasure({ erasure_scheduled_at: result.erasure_scheduled_at });
      } else {
        setPendingErasure(null);
      }
    } catch {
      setPendingErasure(null);
    } finally {
      setStatusLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isValidUUID) {
      void checkStatus(customerId);
    } else {
      setPendingErasure(null);
    }
  }, [customerId, isValidUUID, checkStatus]);

  const handleExport = () => {
    exportCustomer(customerId, {
      onSuccess: (data) => {
        setExportData(data.export);
      },
    });
  };

  const handleScheduleErasure = () => {
    if (confirmText !== 'SCHEDULE ERASURE') return;
    scheduleErasure(customerId, {
      onSuccess: (data) => {
        setPendingErasure({ erasure_scheduled_at: data.erasure_scheduled_at });
        setConfirmText('');
      },
    });
  };

  const handleCancelErasure = () => {
    cancelErasure(customerId, {
      onSuccess: () => {
        setPendingErasure(null);
      },
    });
  };

  const handleDownloadJSON = () => {
    if (!exportData) return;
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `customer_${customerId}_export.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const erasureDate = pendingErasure?.erasure_scheduled_at
    ? new Date(pendingErasure.erasure_scheduled_at).toLocaleString()
    : null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Privacy Controls</h1>
        <p className="text-muted-foreground mt-1">
          Data access, export, consent, and erasure requests
        </p>
      </div>

      {/* Privacy operation badges */}
      <div className="flex flex-wrap gap-2">
        {['Access', 'Export', 'Consent', 'Erasure'].map((tag) => (
          <span key={tag} className="inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
            <CheckCircle2 className="w-3 h-3 mr-1" />
            {tag}
          </span>
        ))}
      </div>

      {/* Policy Info Card */}
      <Card className="border-indigo-500/20 bg-indigo-500/5">
        <CardContent className="pt-6 flex items-start gap-3">
          <Info className="w-5 h-5 text-indigo-400 shrink-0 mt-0.5" />
          <div className="text-sm text-indigo-300 space-y-1">
            <p className="font-semibold">Data Processing Configuration:</p>
            <ul className="list-disc pl-4 space-y-1 text-xs text-indigo-300/90">
              <li>
                <strong>72-Hour Safety Holding Period:</strong> Erasure requests are not immediate.
                A 72-hour safety delay is applied so an authorised admin can cancel the application deletion request.
              </li>
              <li>
                <strong>Cancellable:</strong> Any scheduled erasure can be cancelled by an authorised
                admin before the window expires.
              </li>
              <li>
                <strong>Restricted Scope:</strong> Actions are applied strictly within the target
                customer's associated business (tenant) boundaries.
              </li>
              <li>
                <strong>Operator Responsibility:</strong> The application provides configurable privacy-oriented
                operations. Deployment operators remain responsible for legal and regulatory compliance.
              </li>
            </ul>
          </div>
        </CardContent>
      </Card>

      {/* Customer ID Input */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Customer Identifier</CardTitle>
          <CardDescription>Enter the UUID of the data subject to process their request.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-3">
            <Input
              placeholder="e.g. 3fa85f64-5717-4562-b3fc-2c963f66afa6"
              value={customerId}
              onChange={(e) => {
                setCustomerId(e.target.value);
                setExportData(null);
                setConfirmText('');
              }}
              className="font-mono text-sm max-w-lg"
            />
            {statusLoading && <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />}
            {customerId && !isValidUUID && (
              <span className="text-destructive text-xs flex items-center whitespace-nowrap">
                <AlertTriangle className="w-3 h-3 mr-1" /> Invalid UUID
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Pending Erasure Banner */}
      {isValidUUID && pendingErasure && (
        <div className="flex items-start gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-4">
          <Clock className="h-5 w-5 text-amber-500 shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-sm font-semibold text-amber-700 dark:text-amber-400">
              Erasure Scheduled
            </p>
            <p className="text-xs text-amber-600/80 dark:text-amber-500/80 mt-0.5">
              The application deletion job is scheduled for <strong>{erasureDate}</strong>. You can cancel it
              before the window closes.
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={handleCancelErasure}
            disabled={isCancelling}
            className="shrink-0 border-amber-500/40 text-amber-700 dark:text-amber-400 hover:bg-amber-500/10"
          >
            {isCancelling
              ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" />
              : <Ban className="w-3.5 h-3.5 mr-1.5" />}
            Cancel Erasure
          </Button>
        </div>
      )}

      {/* Action Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Export Card */}
        <Card className="border-t-4 border-t-indigo-500">
          <CardHeader>
            <CardTitle className="flex items-center text-base">
              <Download className="w-4 h-4 mr-2 text-indigo-500" />
              Data Export
            </CardTitle>
            <CardDescription>
              Export the customer profile, messages, bookings, notes, and tags supported by the application as structured JSON.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Button
              onClick={handleExport}
              disabled={!isValidUUID || isExporting}
              className="w-full bg-indigo-600 hover:bg-indigo-700 text-white"
            >
              <FileJson className={`w-4 h-4 mr-2 ${isExporting ? 'animate-pulse' : ''}`} />
              {isExporting ? 'Exporting...' : 'Export Customer Data'}
            </Button>
            {exportData && (
              <div className="space-y-3">
                <div className="rounded-md bg-muted/50 border border-border p-3 max-h-[300px] overflow-auto">
                  <pre className="text-xs font-mono text-muted-foreground whitespace-pre-wrap">
                    {JSON.stringify(exportData, null, 2)}
                  </pre>
                </div>
                <Button variant="outline" size="sm" onClick={handleDownloadJSON} className="w-full">
                  <Download className="w-3.5 h-3.5 mr-2" />
                  Download as JSON
                </Button>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Erasure Card */}
        <Card className={`border-t-4 ${pendingErasure ? 'border-t-amber-500' : 'border-t-destructive'}`}>
          <CardHeader>
            <CardTitle className="flex items-center text-base">
              <Trash2 className={`w-4 h-4 mr-2 ${pendingErasure ? 'text-amber-500' : 'text-destructive'}`} />
              Data Deletion Request
            </CardTitle>
            <CardDescription>
              Schedule an application data-deletion job after a 72-hour safety holding period. Deletion coverage
              may depend on backups, logs, external providers, and deployment-specific retention procedures.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="bg-destructive/5 border border-destructive/20 rounded-md p-3">
              <div className="flex items-start">
                <ShieldAlert className="w-4 h-4 text-destructive mr-2 mt-0.5 shrink-0" />
                <div className="text-xs text-destructive/80">
                  <strong>Application deletion begins after 72h.</strong> The operation targets supported customer records.
                  Review backup, log, and provider retention separately. The request can be cancelled before it begins.
                </div>
              </div>
            </div>

            {isValidUUID && !pendingErasure && (
              <div className="space-y-2">
                <label className="text-xs text-muted-foreground block">
                  Type <span className="font-semibold text-destructive">SCHEDULE ERASURE</span> to confirm:
                </label>
                <Input
                  placeholder="SCHEDULE ERASURE"
                  value={confirmText}
                  onChange={(e) => setConfirmText(e.target.value)}
                  disabled={isScheduling}
                  className="text-xs uppercase font-mono border-destructive/30 focus-visible:ring-destructive"
                />
              </div>
            )}

            {pendingErasure ? (
              <div className="flex items-center gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-600 dark:text-amber-400">
                <Clock className="w-4 h-4 shrink-0" />
                Erasure already scheduled — use "Cancel Erasure" above to abort.
              </div>
            ) : (
              <Button
                variant="destructive"
                onClick={handleScheduleErasure}
                disabled={!isValidUUID || isScheduling || confirmText !== 'SCHEDULE ERASURE'}
                className="w-full"
              >
                <Clock className={`w-4 h-4 mr-2 ${isScheduling ? 'animate-pulse' : ''}`} />
                {isScheduling ? 'Scheduling...' : 'Schedule Data Erasure (72h Lock)'}
              </Button>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
