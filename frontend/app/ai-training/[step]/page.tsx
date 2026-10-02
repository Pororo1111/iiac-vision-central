import { notFound, redirect } from "next/navigation";
import CollectionPage from "../../components/collection-page";
import AiTrainingPage from "../../components/ai-training-page";

const stages: Record<string, { title: string; status: string }> = {
  collect: { title: "이미지 수집", status: "" },
  label: { title: "자동 라벨링", status: "" },
};
export default async function Page({ params }: { params: Promise<{ step: string }> }) {
  const { step } = await params;
  if (step === "analyze" || step === "review") redirect("/ai-training/label");
  const stage = stages[step];
  if (stage) return <CollectionPage key={step} stage={step} title={stage.title} initialStatus={stage.status} />;
  if (step === "dataset" || step === "train" || step === "deploy") return <AiTrainingPage detailStep={step} />;
  notFound();
}
