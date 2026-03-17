import { RouterProvider } from 'react-router';
import { router } from './routes';
import { useConnectionStatus } from './api/hooks';

function ConnectionBanner() {
  const online = useConnectionStatus();
  if (online) return null;
  return (
    <div className="fixed top-0 left-0 right-0 z-50 bg-red-600 text-white text-center py-1.5 text-sm font-medium">
      Connection lost — reconnecting to server...
    </div>
  );
}

export default function App() {
  return (
    <>
      <ConnectionBanner />
      <RouterProvider router={router} />
    </>
  );
}
