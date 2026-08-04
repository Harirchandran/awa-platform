import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import type { WhatsAppAccount } from '../api';
import { Phone, CheckCircle, XCircle } from 'lucide-react';
import { format } from 'date-fns';
interface Props {
  account: WhatsAppAccount;
}
export function WhatsAppAccountCard({ account }: Props) {
  const isConnected = account.status === 'connected';
  return (
    <Card className={`border-l-4 ${isConnected ? 'border-l-emerald-500' : 'border-l-red-500'}`}>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <div className={`p-2 rounded-full ${isConnected ? 'bg-emerald-500/10 text-emerald-500' : 'bg-red-500/10 text-red-500'}`}>
              <Phone className="h-5 w-5" />
            </div>
            <div>
              <CardTitle className="text-xl">{account.display_name || 'WhatsApp Business'}</CardTitle>
              <CardDescription className="text-base font-medium text-foreground">{account.phone_number}</CardDescription>
            </div>
          </div>
          <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${isConnected ? 'bg-emerald-500/10 text-emerald-500' : 'bg-red-500/10 text-red-500'}`}>
            {isConnected ? (
              <><CheckCircle className="w-3 h-3 mr-1" /> Connected</>
            ) : (
              <><XCircle className="w-3 h-3 mr-1" /> Disconnected</>
            )}
          </span>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm mt-4 p-4 bg-muted/30 rounded-lg">
          <div>
            <span className="text-muted-foreground block mb-1">Phone Number ID</span>
            <code className="px-2 py-1 bg-muted rounded text-xs select-all">
              {account.phone_number_id}
            </code>
          </div>
          {account.waba_id ? (
            <div>
              <span className="text-muted-foreground block mb-1">WhatsApp Business Account</span>
              <code className="px-2 py-1 bg-muted rounded text-xs select-all">
                {account.waba_id}
              </code>
            </div>
          ) : null}
          <div>
            <span className="text-muted-foreground block mb-1">Last Outbound Message</span>
            <span className="font-medium">
              {account.last_message_at
                ? format(new Date(account.last_message_at), 'MMM d, yyyy h:mm a')
                : 'No messages yet'}
            </span>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}