content = open('../frontend/src/strings/verdict.ts', 'r').read()
if 'forYou: {' not in content:
    content = content.replace(
        '  grade: {',
        '  forYou: {\n    title: \'FOR YOU\',\n  },\n  grade: {'
    )
    open('../frontend/src/strings/verdict.ts', 'w').write(content)
    print("Added forYou to strings")
