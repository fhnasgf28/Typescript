describe('array', function () {
    it('should same with javascrirpt', function () {
        const names: string[] = ['a', 'b', 'c'];
        const values: number[] = [1, 2, 3, 4, 5 ];

        console.info(names);
        console.info(values);
    })

    it("should support readonly array", function () {
        const hobbies: ReadonlyArray<string> = ["Membaca", "Menulis"];
        console.info(hobbies);
        console.info(hobbies[1])
        console.info(hobbies[2])
    })

    it('should support tuple', function() {
        const person: readonly [ string, string, number] = ["farhan", "assegaf", 28];

        console.info(person);
        console.info(person[1]);
    })
})