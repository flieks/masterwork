import { Toaster as Sonner, type ToasterProps } from 'sonner';

/** App-wide toast host. `theme="system"` follows the OS colour scheme. */
export function Toaster(props: ToasterProps) {
  return <Sonner theme="system" position="bottom-right" richColors closeButton {...props} />;
}

export { toast } from 'sonner';
