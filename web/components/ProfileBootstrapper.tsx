'use client';

import { useEffect } from 'react';
import { useAtlas } from '@/lib/store';
import { resolveInitialProfile } from '@/lib/profile';

export function ProfileBootstrapper() {
	const setProfile = useAtlas((s) => s.setProfile);

	useEffect(() => {
		setProfile(resolveInitialProfile());
	}, [setProfile]);

	return null;
}