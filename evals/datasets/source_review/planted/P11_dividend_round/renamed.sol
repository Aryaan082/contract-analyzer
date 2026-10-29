// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract ID1 {
    address[] public ID2;
    mapping(address => uint256) public ID3;
    uint256 public ID4;

    function ID5() external payable {
        require(msg.value > 0, "empty");
        if (ID3[msg.sender] == 0) ID2.push(msg.sender);
        ID3[msg.sender] += msg.value;
        ID4 += msg.value;
    }

    function ID6() external {
        uint256 ID7 = address(this).balance - ID4;
        for (uint256 ID8 = 0; ID8 < ID2.length; ID8++) {
            address ID9 = ID2[ID8];
            payable(ID9).transfer(ID7 * ID3[ID9] / ID4);
        }
    }
}
