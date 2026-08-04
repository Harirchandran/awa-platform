import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight, MessageCircle, MessagesSquare } from 'lucide-react';
import { Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { formatDateOnlyLabel } from '@/lib/date-only';
import type { DailyCostRow } from '../api';
interface CostChartProps {
  data: DailyCostRow[];
}
export function CostChart({ data }: CostChartProps) {
  const chartHostRef = useRef<HTMLDivElement | null>(null);
  const [chartWidth, setChartWidth] = useState(0);
  useEffect(() => {
    const updateWidth = () => {
      const width = chartHostRef.current?.getBoundingClientRect().width ?? 0;
      setChartWidth(Math.floor(width));
    };
    updateWidth();
    window.addEventListener('resize', updateWidth);
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(updateWidth) : null;
    if (chartHostRef.current && observer) {
      observer.observe(chartHostRef.current);
    }
    return () => {
      window.removeEventListener('resize', updateWidth);
      observer?.disconnect();
    };
  }, []);
  if (!data || data.length === 0) {
    return (
      <Card className="col-span-4 overflow-hidden">
        <CardHeader>
          <CardTitle>Conversation Activity</CardTitle>
          <CardDescription>Customer messages handled in the selected 30-day window</CardDescription>
        </CardHeader>
        <CardContent className="flex min-h-[300px] items-center justify-center px-4 pb-8 pt-1 sm:min-h-[320px] sm:px-6">
          <div className="mx-auto flex max-w-md flex-col items-center text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-md border border-primary/20 bg-primary/10">
              <MessagesSquare className="h-5 w-5 text-primary" />
            </div>
            <h3 className="mt-4 text-[17px] font-semibold text-foreground">No Activity Yet</h3>
            <p className="mt-2 text-[13px] leading-relaxed text-muted-foreground">
              Customer messages and AI usage will appear here after the first AWA Chat or WhatsApp conversation.
            </p>
            <Button asChild size="sm" className="mt-5 w-full sm:w-auto">
              <Link to="/dashboard/awa-chat">
                Open AWA Chat
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </Button>
            <Button asChild variant="outline" size="sm" className="mt-2 w-full sm:w-auto">
              <Link to="/dashboard/whatsapp">
                <MessageCircle className="h-3.5 w-3.5" />
                Connect WhatsApp
              </Link>
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }
  // Format date to short format "MMM DD"
  const chartData = data.map(d => ({
    ...d,
    dateLabel: formatDateOnlyLabel(d.date, { month: 'short', day: 'numeric' }),
  })).reverse(); // Reverse if Backend sends DESC -> ASC for charting left to right
  return (
    <Card className="col-span-4 min-w-0">
      <CardHeader>
        <CardTitle>Conversation Activity</CardTitle>
        <CardDescription>Customer messages handled in the selected 30-day window</CardDescription>
      </CardHeader>
      <CardContent className="h-[350px] min-w-0">
        <div ref={chartHostRef} className="h-full min-w-0">
          {chartWidth > 0 ? (
          <LineChart data={chartData} width={chartWidth} height={300}>
            <XAxis
              dataKey="dateLabel"
              fontSize={12}
              tickLine={false}
              axisLine={false}
              stroke="hsl(var(--muted-foreground))"
            />
            <YAxis
              fontSize={12}
              tickLine={false}
              axisLine={false}
              stroke="hsl(var(--muted-foreground))"
              allowDecimals={false}
            />
            <Tooltip
              contentStyle={{ backgroundColor: 'hsl(var(--card))', border: '1px solid hsl(var(--border))', borderRadius: '8px' }}
              itemStyle={{ color: 'hsl(var(--foreground))' }}
            />
            <Line
              type="monotone"
              dataKey="message_count"
              name="Messages"
              stroke="hsl(var(--primary))"
              strokeWidth={3}
              dot={{ r: 4, strokeWidth: 2 }}
              activeDot={{ r: 6, strokeWidth: 0, fill: "hsl(var(--primary))" }}
            />
          </LineChart>
          ) : (
            <div className="h-full w-full rounded-md bg-muted/20" />
          )}
        </div>
      </CardContent>
    </Card>
  );
}
