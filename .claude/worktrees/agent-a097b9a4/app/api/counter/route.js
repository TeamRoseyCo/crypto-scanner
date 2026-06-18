import { NextResponse } from "next/server";

export async function POST(request) {
    try {
        const body = await request.json();

        const { action, counterId } = body;

        console.log(
            `${
                action === "increment" ? "🤩" : "😭"
            } Counter ${counterId}  ${action}ed`
        );
        //You can do anything here!
        // - Save information in Database
        // - Compute data
        // - Call another API like OpenAI

        return NextResponse.json({
            success: true,
            data: { action, counterId },
    });
} catch (error) {
        return NextResponse.json(
            { success: false, error: error.message },
            { status: 500}
        );
    }
}