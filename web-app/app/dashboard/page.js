import Image from "next/image";
import nextImg from "@/app/next.png";

export default function Dashboard() {
  return (
    <div className="p-8">
        <Image
        src={nextImg}
        alt="NextJS demo"
        width={300}
        height={300}
        className="w-full rounded-lg border border-gray-200 shadow-md"
        />
    </div>
  );
}