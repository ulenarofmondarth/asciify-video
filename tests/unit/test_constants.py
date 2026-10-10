"""Stage/flag table registrations."""



from asciify_video import constants


class TestStageFlagTables:
    def test_flag_map_registrations_complete(self):
        # Rendering flags must all be registered, per README checklist
        rendering = constants.STAGE_FLAG_MAP['rendering']
        for flag in ('--render-font-size', '--colorized', '--font-color',
                     '--leave-black-empty', '--black-threshold',
                     '--background-color', '--background-alpha'):
            assert flag in rendering, flag
        assert set(constants.REUSE_SKIPPED_STAGES['final']) == \
            {'keying', 'conversion', 'rendering'}
