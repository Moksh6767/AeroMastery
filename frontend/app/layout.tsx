import "./globals.css";
import "maplibre-gl/dist/maplibre-gl.css";
export const metadata = {
  title: "Relief Atlas | Civilian Response",
  description:
    "Synthetic civilian relief scheduling and transparent human review",
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
