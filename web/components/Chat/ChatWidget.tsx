'use client';

import { useState, useRef, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useAtlas } from '@/lib/store';
import { streamChat } from '@/lib/api';

interface Message {
	role: 'user' | 'assistant';
	content: string;
}

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
				className={`fixed bottom-6 right-6 w-14 h-14 bg-blue-600 text-white rounded-full shadow-xl hover:bg-blue-700 transition flex items-center justify-center z-40 ${isOpen ? 'hidden' : ''}`}
			>
				<svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" /></svg>
			</button>

			{isOpen && (
				<div className="fixed z-50 bg-white shadow-2xl border border-gray-200 flex flex-col overflow-hidden transition-all
					bottom-0 right-0 w-full h-[85vh] rounded-t-2xl
					md:bottom-6 md:right-6 md:w-96 md:h-[600px] md:rounded-2xl"
				>
				
					<div className="bg-blue-600 text-white p-4 flex justify-between items-center shrink-0">
						<div>
							<h3 className="font-bold">Атлас ИИ</h3>
							<p className="text-xs text-blue-100">Спросите о любом месте СКФО</p>
						</div>
						<button onClick={() => setIsOpen(false)} className="text-blue-100 hover:text-white p-1">
							<svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
							</svg>
						</button>
					</div>

					<div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-4 bg-gray-50">
						{messages.length === 0 && (
							<div className="text-center text-gray-400 mt-10 px-4">
								<p className="text-sm">Спросите о методике индекса или доступности территорий Северного Кавказа.</p>
								<p className="text-sm mt-2">Спросите, например: «Где в Дагестане лучше всего с больницами?»</p>
							</div>
						)}

						{messages.map((m, i) => (
							<div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
								<div className={`max-w-[95%] p-3 rounded-2xl text-sm ${
								m.role === 'user' 
									? 'bg-blue-600 text-white rounded-br-none whitespace-pre-wrap' 
									: 'bg-white border text-gray-800 rounded-bl-none shadow-sm'
								}`}>
									{m.role === 'user' ? (
										m.content
									) : m.content ? (
										<div className="markdown-body">
											<ReactMarkdown 
												remarkPlugins={[remarkGfm]}
												components={{
													// Кастомные стили для элементов Markdown
													p: ({node, ...props}) => <p className="mb-2 last:mb-0" {...props} />,
													ul: ({node, ...props}) => <ul className="list-disc pl-5 mb-2 space-y-1" {...props} />,
													ol: ({node, ...props}) => <ol className="list-decimal pl-5 mb-2 space-y-1" {...props} />,
													li: ({node, ...props}) => <li className="" {...props} />,
													strong: ({node, ...props}) => <strong className="font-bold text-gray-900" {...props} />,
													h1: ({node, ...props}) => <h1 className="text-base font-bold mb-2 mt-4 first:mt-0" {...props} />,
													h2: ({node, ...props}) => <h2 className="text-sm font-bold mb-2 mt-4 first:mt-0" {...props} />,
													h3: ({node, ...props}) => <h3 className="text-sm font-semibold mb-1 mt-3 first:mt-0" {...props} />,
													table: ({node, ...props}) => (
														<div className="overflow-x-auto my-2">
															<table className="min-w-full divide-y divide-gray-200 border text-xs" {...props} />
														</div>
													),
													th: ({node, ...props}) => <th className="px-2 py-1 bg-gray-100 font-medium text-left border" {...props} />,
													td: ({node, ...props}) => <td className="px-2 py-1 border" {...props} />,
													code: ({node, inline, ...props}: any) => 
														inline 
															? <code className="bg-gray-100 px-1 rounded text-xs" {...props} />
															: <pre className="bg-gray-800 text-gray-100 p-2 rounded text-xs overflow-x-auto my-2"><code {...props} /></pre>
												}}
											>
												{m.content}
											</ReactMarkdown>
										</div>
									) : (
										<span className="animate-pulse text-gray-400">Проверяю данные...</span>
									)}
								</div>
							</div>
						))}
						
						{error && (
							<div className="text-center text-xs text-red-500 bg-red-50 p-2 rounded">
								{error}
							</div>
						)}
					</div>

					<div className="p-3 bg-white border-t shrink-0">
						<div className="flex gap-2">
							<textarea
								value={input}
								onChange={(e) => setInput(e.target.value)}
								onKeyDown={handleKeyDown}
								placeholder="Задайте вопрос..."
								className="flex-1 border rounded-xl px-3 py-2 text-sm resize-none focus:ring-2 focus:ring-blue-500 outline-none max-h-24"
								rows={1}
								disabled={isLoading}
							/>
							<button
								onClick={handleSend}
								disabled={!input.trim() || isLoading}
								className="bg-blue-600 text-white p-2 rounded-xl hover:bg-blue-700 disabled:opacity-50 transition flex items-center justify-center w-10 h-10 shrink-0"
							>
								<svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" /></svg>
							</button>
						</div>
					</div>
				</div>
			)}
		</>
	);
}