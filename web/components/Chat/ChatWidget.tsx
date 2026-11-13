'use client';

import { useState, useRef, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useAtlas } from '@/lib/store';
import { streamChat } from '@/lib/api';
import { BrandMark } from '@/components/UI/Brand';
import { Icon } from '@/components/UI/Icon';

interface Message {
	role: 'user' | 'assistant';
	content: string;
}

const EXAMPLES = [
	'Где в Дагестане лучше всего с больницами?',
	'Как считается индекс доступности?',
];

export function ChatWidget() {
	const profile = useAtlas((s) => s.profile);
	const [isOpen, setIsOpen] = useState(false);
	const [messages, setMessages] = useState<Message[]>([]);
	const [input, setInput] = useState('');
	const [isLoading, setIsLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	const scrollRef = useRef<HTMLDivElement>(null);
	const messagesRef = useRef<Message[]>([]);

	useEffect(() => {
		messagesRef.current = messages;
	}, [messages]);

	useEffect(() => {
		if (scrollRef.current) {
			scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
		}
	}, [messages, isLoading]);

	const handleSend = async () => {
		const text = input.trim();
		if (!text || isLoading) return;

		const answered = messagesRef.current.filter((m) => m.content.trim());
		const historyToSend: Message[] = [...answered, { role: 'user', content: text }];

		setMessages([...historyToSend, { role: 'assistant', content: '' }]);
		setInput('');
		setIsLoading(true);
		setError(null);

		let assistantText = '';
		try {
			await streamChat(historyToSend, profile, (chunk) => {
				assistantText += chunk;
				setMessages((prev) => {
					const newArr = [...prev];
					newArr[newArr.length - 1] = { role: 'assistant', content: assistantText };
					return newArr;
				});
			});
			if (!assistantText.trim()) throw new Error('Ассистент не дал ответа — попробуйте переформулировать вопрос');
		} catch (e: unknown) {
			console.error(e);
			setError(e instanceof Error ? e.message : 'Произошла ошибка при обращении к ИИ');
			setMessages(answered);
			setInput(text);
		} finally {
			setIsLoading(false);
		}
	};

	const handleKeyDown = (e: React.KeyboardEvent) => {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			handleSend();
		}
	};

	return (
		<>
			<button
				onClick={() => setIsOpen(true)}
				aria-label="Открыть чат с ассистентом"
				className={`group fixed bottom-6 right-6 z-40 grid h-14 w-14 place-items-center rounded-2xl bg-brand-gradient text-white shadow-glow transition-all duration-300 ease-spring hover:-translate-y-0.5 hover:scale-105 active:scale-95 ${isOpen ? 'hidden' : ''}`}
			>
				<span className="absolute inset-0 rounded-2xl bg-[radial-gradient(circle_at_30%_20%,rgb(255_255_255/0.4),transparent_60%)]" />
				<Icon name="chat" className="relative h-6 w-6" />
			</button>

			{isOpen && (
				<div
					className="glass-strong fixed z-50 flex flex-col overflow-hidden animate-sheet-up
						bottom-0 right-0 h-[85vh] w-full rounded-t-3xl
						md:bottom-6 md:right-6 md:h-[620px] md:w-[25rem] md:rounded-3xl md:animate-scale-in"
				>
					<div className="relative shrink-0 overflow-hidden bg-brand-gradient p-4 text-white">
						<div className="absolute -right-10 -top-16 h-40 w-40 rounded-full bg-white/15 blur-2xl" />
						<div className="relative flex items-center gap-3">
							<BrandMark className="h-10 w-10 ring-2 ring-white/40" />
							<div className="min-w-0 flex-1">
								<h3 className="font-display text-[15px] font-semibold tracking-tight">Атлас ИИ</h3>
								<p className="text-xs font-medium text-white/80">Спросите о любом месте СКФО</p>
							</div>
							<button onClick={() => setIsOpen(false)} aria-label="Закрыть чат" className="grid h-9 w-9 place-items-center rounded-xl text-white/80 transition hover:bg-white/15 hover:text-white">
								<Icon name="x" className="h-5 w-5" />
							</button>
						</div>
					</div>

					<div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto bg-gradient-to-b from-ink-50/60 to-white/40 p-4 scrollbar-soft">
						{messages.length === 0 && (
							<div className="mt-6 px-2 text-center animate-fade-up">
								<div className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-brand-50 text-brand-500 ring-1 ring-brand-100">
									<Icon name="sparkles" className="h-7 w-7" />
								</div>
								<p className="mt-4 text-sm font-medium leading-relaxed text-ink-600">
									Спросите о методике индекса или доступности территорий Северного Кавказа.
								</p>
								<div className="mt-4 flex flex-col items-center gap-2">
									{EXAMPLES.map((q) => (
										<button
											key={q}
											onClick={() => setInput(q)}
											className="rounded-full bg-white px-3.5 py-2 text-xs font-semibold text-brand-700 shadow-soft ring-1 ring-brand-100 transition hover:-translate-y-0.5 hover:ring-brand-200"
										>
											{q}
										</button>
									))}
								</div>
							</div>
						)}

						{messages.map((m, i) => (
							<div key={i} className={`flex animate-fade-up ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
								<div
									className={`max-w-[92%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed ${
										m.role === 'user'
											? 'whitespace-pre-wrap rounded-br-md bg-brand-gradient text-white shadow-glow'
											: 'rounded-bl-md bg-white text-ink-800 shadow-soft ring-1 ring-ink-100'
									}`}
								>
									{m.role === 'user' ? (
										m.content
									) : m.content ? (
										<div className="markdown-body">
											<ReactMarkdown
												remarkPlugins={[remarkGfm]}
												components={{
													p: ({ node, ...props }) => <p className="mb-2 last:mb-0" {...props} />,
													ul: ({ node, ...props }) => <ul className="mb-2 list-disc space-y-1 pl-5 marker:text-brand-400" {...props} />,
													ol: ({ node, ...props }) => <ol className="mb-2 list-decimal space-y-1 pl-5 marker:text-brand-400" {...props} />,
													li: ({ node, ...props }) => <li {...props} />,
													strong: ({ node, ...props }) => <strong className="font-bold text-ink-900" {...props} />,
													a: ({ node, ...props }) => <a className="font-semibold text-brand-600 underline decoration-brand-200 underline-offset-2" target="_blank" rel="noopener noreferrer" {...props} />,
													h1: ({ node, ...props }) => <h1 className="mb-2 mt-4 text-base font-bold first:mt-0" {...props} />,
													h2: ({ node, ...props }) => <h2 className="mb-2 mt-4 text-sm font-bold first:mt-0" {...props} />,
													h3: ({ node, ...props }) => <h3 className="mb-1 mt-3 text-sm font-semibold first:mt-0" {...props} />,
													table: ({ node, ...props }) => (
														<div className="my-2 overflow-x-auto rounded-xl ring-1 ring-ink-100">
															<table className="min-w-full divide-y divide-ink-100 text-xs" {...props} />
														</div>
													),
													th: ({ node, ...props }) => <th className="bg-ink-50 px-2 py-1.5 text-left font-bold text-ink-700" {...props} />,
													td: ({ node, ...props }) => <td className="border-t border-ink-100 px-2 py-1.5" {...props} />,
													// Blocks come as <pre><code>; inline code is a bare <code>.
													pre: ({ node, ...props }) => (
														<pre className="my-2 overflow-x-auto rounded-xl bg-ink-900 p-3 text-xs text-ink-100 [&_code]:bg-transparent [&_code]:p-0 [&_code]:text-inherit" {...props} />
													),
													code: ({ node, ...props }) => <code className="rounded-md bg-ink-100 px-1.5 py-0.5 text-[0.85em] text-ink-800" {...props} />,
												}}
											>
												{m.content}
											</ReactMarkdown>
										</div>
									) : (
										<span className="flex items-center gap-2 py-1 text-ink-400">
											<span className="flex gap-1">
												<span className="h-1.5 w-1.5 rounded-full bg-brand-400 animate-typing" />
												<span className="h-1.5 w-1.5 rounded-full bg-brand-400 animate-typing [animation-delay:150ms]" />
												<span className="h-1.5 w-1.5 rounded-full bg-brand-400 animate-typing [animation-delay:300ms]" />
											</span>
											<span className="text-xs font-medium">Проверяю данные…</span>
										</span>
									)}
								</div>
							</div>
						))}

						{error && (
							<div className="flex items-start gap-2 rounded-2xl bg-rose-50 p-3 text-xs font-medium text-rose-700 ring-1 ring-rose-100">
								<Icon name="alert" className="h-4 w-4 shrink-0" />
								{error}
							</div>
						)}
					</div>

					<div className="shrink-0 border-t border-white/80 bg-white/70 p-3">
						<div className="flex items-end gap-2 rounded-2xl bg-white p-1.5 shadow-soft ring-1 ring-ink-100 transition focus-within:ring-2 focus-within:ring-brand-200">
							<textarea
								value={input}
								onChange={(e) => setInput(e.target.value)}
								onKeyDown={handleKeyDown}
								placeholder="Задайте вопрос…"
								className="max-h-24 flex-1 resize-none bg-transparent px-2.5 py-2 text-sm text-ink-900 outline-none placeholder:text-ink-400"
								rows={1}
								disabled={isLoading}
							/>
							<button
								onClick={handleSend}
								disabled={!input.trim() || isLoading}
								aria-label="Отправить"
								className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-brand-gradient text-white shadow-glow transition hover:brightness-110 disabled:opacity-40 disabled:shadow-none"
							>
								<Icon name="send" className="h-[18px] w-[18px]" />
							</button>
						</div>
					</div>
				</div>
			)}
		</>
	);
}
