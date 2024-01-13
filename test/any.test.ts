describe('Any', function () {
    it('Should support in typescript', function () {
        const person: any = {
            id: 1,
            name: 'John',
            age: 21,
        }

        person.age = 31
        person.address = "Jakarta";

        console.log(person)
    })
})