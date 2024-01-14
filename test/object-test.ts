describe('Object', function() {
    it('should support in typescript', function() {
        const person : { id: string, name:  string} = {
            id: '1',
            name: "Farhan"
        }

        console.info(person);
    })

})