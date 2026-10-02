import DeviceDetail from "../../components/device-detail";

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <DeviceDetail deviceId={decodeURIComponent(id)} />;
}
