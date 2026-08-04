import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import {
  Accessibility,
  Check,
  Eye,
  ListPlus,
  Monitor,
  Moon,
  RotateCcw,
  Sun,
  Type,
  ZapOff,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Switch } from '@/components/ui/switch';
import { cn } from '@/lib/utils';
type ThemePreference = 'light' | 'dark' | 'system';
type TextScalePreference = 'standard' | 'large';
type DisplayPreferences = {
  theme: ThemePreference;
  highContrast: boolean;
  textScale: TextScalePreference;
  comfortableSpacing: boolean;
  reducedMotion: boolean;
};
type DisplayPreferencesContextValue = {
  preferences: DisplayPreferences;
  resolvedTheme: 'light' | 'dark';
  updatePreferences: (patch: Partial<DisplayPreferences>) => void;
  resetPreferences: () => void;
};
const STORAGE_KEY = 'awa_display_preferences';
const DEFAULT_PREFERENCES: DisplayPreferences = {
  theme: 'dark',
  highContrast: false,
  textScale: 'standard',
  comfortableSpacing: false,
  reducedMotion: false,
};
const DisplayPreferencesContext = createContext<DisplayPreferencesContextValue | null>(null);
function normalizePreferences(value: Partial<DisplayPreferences> | null): DisplayPreferences {
  return {
    theme: value?.theme === 'light' || value?.theme === 'system' ? value.theme : 'dark',
    highContrast: Boolean(value?.highContrast),
    textScale: value?.textScale === 'large' ? 'large' : 'standard',
    comfortableSpacing: Boolean(value?.comfortableSpacing),
    reducedMotion: Boolean(value?.reducedMotion),
  };
}
function readStoredPreferences() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return normalizePreferences(raw ? JSON.parse(raw) : DEFAULT_PREFERENCES);
  } catch {
    return DEFAULT_PREFERENCES;
  }
}
function getSystemTheme(): 'light' | 'dark' {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}
function resolveTheme(theme: ThemePreference, systemTheme = getSystemTheme()): 'light' | 'dark' {
  if (theme === 'system') {
    return systemTheme;
  }
  return theme;
}
function applyPreferences(preferences: DisplayPreferences, resolvedTheme = resolveTheme(preferences.theme)) {
  const root = document.documentElement;
  root.classList.toggle('dark', resolvedTheme === 'dark');
  root.classList.toggle('a11y-high-contrast', preferences.highContrast);
  root.classList.toggle('a11y-text-large', preferences.textScale === 'large');
  root.classList.toggle('a11y-comfortable', preferences.comfortableSpacing);
  root.classList.toggle('a11y-reduced-motion', preferences.reducedMotion);
  root.dataset.theme = resolvedTheme;
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  themeMeta?.setAttribute('content', resolvedTheme === 'dark' ? '#0a0a0a' : '#f8fafc');
}
export function DisplayPreferencesProvider({ children }: { children: ReactNode }) {
  const [preferences, setPreferences] = useState<DisplayPreferences>(() => readStoredPreferences());
  const [systemTheme, setSystemTheme] = useState<'light' | 'dark'>(() => getSystemTheme());
  const resolvedTheme = useMemo(() => resolveTheme(preferences.theme, systemTheme), [preferences.theme, systemTheme]);
  useEffect(() => {
    applyPreferences(preferences, resolvedTheme);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences));
    } catch {
      // Preference persistence should never block the interface.
    }
  }, [preferences, resolvedTheme]);
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const syncSystemTheme = () => setSystemTheme(media.matches ? 'dark' : 'light');
    syncSystemTheme();
    media.addEventListener('change', syncSystemTheme);
    return () => media.removeEventListener('change', syncSystemTheme);
  }, []);
  const updatePreferences = useCallback((patch: Partial<DisplayPreferences>) => {
    setPreferences((current) => ({ ...current, ...patch }));
  }, []);
  const resetPreferences = useCallback(() => {
    setPreferences(DEFAULT_PREFERENCES);
  }, []);
  const value = useMemo(
    () => ({ preferences, resolvedTheme, updatePreferences, resetPreferences }),
    [preferences, resetPreferences, resolvedTheme, updatePreferences]
  );
  return <DisplayPreferencesContext.Provider value={value}>{children}</DisplayPreferencesContext.Provider>;
}
function useDisplayPreferences() {
  const context = useContext(DisplayPreferencesContext);
  if (!context) {
    throw new Error('useDisplayPreferences must be used within DisplayPreferencesProvider');
  }
  return context;
}
const themeOptions: Array<{
  value: ThemePreference;
  label: string;
  icon: typeof Sun;
}> = [
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
  { value: 'system', label: 'System', icon: Monitor },
];
type DisplayPreferencesToggleProps = {
  mode?: 'fixed' | 'inline';
};
export function DisplayPreferencesToggle({ mode = 'fixed' }: DisplayPreferencesToggleProps) {
  const { preferences, resolvedTheme, updatePreferences, resetPreferences } = useDisplayPreferences();
  const fixed = mode === 'fixed';
  const themeStatus = preferences.theme === 'system' ? `System: ${resolvedTheme}` : preferences.theme;
  return (
    <div className={cn(fixed ? 'fixed right-4 top-16 z-50' : 'relative z-40', 'print:hidden')}>
      <Popover>
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant="outline"
            size="icon"
            className={cn(
              'border-border bg-background/95 backdrop-blur',
              fixed ? 'h-10 w-10 rounded-full shadow-lg' : 'h-9 w-9 rounded-md shadow-none'
            )}
            aria-label="Open display and accessibility settings"
            title="Display & accessibility"
          >
            <Accessibility className="h-4 w-4" />
          </Button>
        </PopoverTrigger>
        <PopoverContent
          side="bottom"
          align="end"
          className="w-[min(calc(100vw-2rem),22rem)] p-0"
        >
          <div className="border-b border-border px-4 py-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-[14px] font-semibold text-foreground">Display & accessibility</h2>
                <p className="mt-0.5 text-[12px] text-muted-foreground">Saved on this device</p>
              </div>
              <span className="rounded-full border border-border px-2 py-1 text-[11px] font-medium capitalize text-muted-foreground">
                {themeStatus}
              </span>
            </div>
          </div>
          <div className="space-y-4 p-4">
            <div>
              <p className="mb-2 text-[12px] font-medium text-muted-foreground">Theme</p>
              <div className="grid grid-cols-3 gap-1 rounded-md border border-border bg-muted/30 p-1" role="radiogroup" aria-label="Theme">
                {themeOptions.map(({ value, label, icon: Icon }) => {
                  const active = preferences.theme === value;
                  return (
                    <button
                      key={value}
                      type="button"
                      role="radio"
                      aria-checked={active}
                      onClick={() => updatePreferences({ theme: value })}
                      className={cn(
                        'flex h-9 min-w-0 items-center justify-center gap-1.5 rounded-sm px-2 text-[12px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                        active
                          ? 'bg-background text-foreground shadow-sm'
                          : 'text-muted-foreground hover:bg-background/70 hover:text-foreground'
                      )}
                    >
                      <Icon className="h-3.5 w-3.5 shrink-0" />
                      <span className="truncate">{label}</span>
                      {active && <Check className="hidden h-3 w-3 shrink-0 sm:block" />}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="space-y-3">
              <PreferenceSwitch
                icon={Eye}
                label="High contrast"
                checked={preferences.highContrast}
                onCheckedChange={(checked) => updatePreferences({ highContrast: checked })}
              />
              <PreferenceSwitch
                icon={Type}
                label="Larger text"
                checked={preferences.textScale === 'large'}
                onCheckedChange={(checked) => updatePreferences({ textScale: checked ? 'large' : 'standard' })}
              />
              <PreferenceSwitch
                icon={ListPlus}
                label="Comfortable spacing"
                checked={preferences.comfortableSpacing}
                onCheckedChange={(checked) => updatePreferences({ comfortableSpacing: checked })}
              />
              <PreferenceSwitch
                icon={ZapOff}
                label="Reduce motion"
                checked={preferences.reducedMotion}
                onCheckedChange={(checked) => updatePreferences({ reducedMotion: checked })}
              />
            </div>
            <Button type="button" variant="outline" size="sm" className="w-full" onClick={resetPreferences}>
              <RotateCcw className="mr-2 h-3.5 w-3.5" />
              Reset display settings
            </Button>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}
function PreferenceSwitch({
  checked,
  icon: Icon,
  label,
  onCheckedChange,
}: {
  checked: boolean;
  icon: typeof Eye;
  label: string;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-border bg-card px-3 py-2.5">
      <div className="flex min-w-0 items-center gap-2">
        <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="truncate text-[13px] font-medium text-foreground">{label}</span>
      </div>
      <Switch checked={checked} onCheckedChange={onCheckedChange} aria-label={label} />
    </div>
  );
}